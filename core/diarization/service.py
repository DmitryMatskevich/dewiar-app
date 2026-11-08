"""Разбиение встречи по участникам над файлами встречи: после записи (prompter._post_transcribe), из карточки
прошлой встречи («Разбить по участникам», «Пересчитать») и правки участников в проигрывателе.

Файлы встречи «<имя>» (как пишет prompter.py):
  Встречи/Аудио/<имя>.wav, Встречи/Аудио/<имя>/Вы.wav, Собеседник.wav
  Встречи/Расшифровки/<имя>.txt, .lines.json, .words.json, .diarization.rttm, .speakers.json
"""
import json
import logging
import re
import wave
from pathlib import Path

import numpy as np

from . import edits as edits_mod
from . import models, pipeline, runner, storage, tracks
from .labels import label as line_label
from .schema import Turn, turns_from_dicts

log = logging.getLogger("dewiar.diarization")
DEFAULTS = {"threshold": 0.4, "merge_threshold": 0.80, "num_threads": 2, "min_duration_sec": 20, "timeout_factor": 0.5}


def config(prompter_cfg):
    cfg = dict(DEFAULTS)
    raw = (prompter_cfg or {}).get("diarization") if isinstance(prompter_cfg, dict) else None
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in DEFAULTS:
                try:
                    cfg[k] = type(DEFAULTS[k])(v)
                except (TypeError, ValueError):
                    pass
    cfg["threshold"] = min(0.95, max(0.1, cfg["threshold"]))
    cfg["merge_threshold"] = min(0.99, max(0.5, cfg["merge_threshold"]))
    cfg["num_threads"] = max(1, min(8, cfg["num_threads"]))
    return cfg


class MeetingFiles:
    def __init__(self, meetings_root, stem):
        root = Path(meetings_root)
        stem = str(stem or "")
        if not stem or "/" in stem or "\\" in stem or stem in (".", ".."):
            raise ValueError("это не встреча")
        self.root, self.stem = root, stem
        self.mix = root / "Аудио" / f"{stem}.wav"
        self.folder = root / "Аудио" / stem
        self.me = self.folder / "Вы.wav"
        self.them = self.folder / "Собеседник.wav"
        self.txt = root / "Расшифровки" / f"{stem}.txt"
        self.lines = self.txt.with_suffix(".lines.json")
        self.words = storage.words_path(self.txt)
        self.rttm = storage.rttm_path(self.txt)
        self.speakers = edits_mod.path_for(self.txt)
        self.log = self.txt.with_suffix(".diarization.log")

    @classmethod
    def explicit(cls, mix, txt, them=None, me=None):
        """Пути из идущей записи (prompter): каталоги могут быть резервными, а не «Встречи/…»."""
        obj = cls.__new__(cls)
        obj.mix, obj.txt = Path(mix), Path(txt)
        obj.root, obj.stem = obj.mix.parent.parent, obj.mix.stem
        obj.folder = obj.mix.parent / obj.mix.stem
        obj.them = Path(them) if them else None          # без дорожек — только общий WAV, чужие файлы не берём
        obj.me = Path(me) if me else None
        obj.lines = obj.txt.with_suffix(".lines.json")
        obj.words = storage.words_path(obj.txt)
        obj.rttm = storage.rttm_path(obj.txt)
        obj.speakers = edits_mod.path_for(obj.txt)
        obj.log = obj.txt.with_suffix(".diarization.log")
        return obj

    @classmethod
    def from_summary(cls, meetings_root, summary_rel):
        rel = str(summary_rel or "").replace("\\", "/")
        name = rel[len("Встречи/"):] if rel.startswith("Встречи/") else rel
        if "/" in name or not name.endswith(".md"):
            raise ValueError("это не итог встречи")
        return cls(meetings_root, name[:-3])

    @staticmethod
    def ok(p):
        return bool(p) and Path(p).is_file() and Path(p).stat().st_size > 44

    def dual(self):
        return self.ok(self.them)

    def source(self):
        return self.them if self.dual() else self.mix


def wav_seconds(path):
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except (OSError, EOFError, wave.Error):
        return 0.0


def wav_embedder(path, speaker):
    """embed(start, end) по отрезку WAV той же моделью CAM++, что у профиля владельца."""
    def embed(start, end):
        try:
            with wave.open(str(path), "rb") as w:
                sr = w.getframerate()
                if sr != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
                    return None
                w.setpos(max(0, int(start * sr)))
                pcm = w.readframes(max(0, int((end - start) * sr)))
            return speaker.embed(pcm)
        except Exception as e:  # noqa: BLE001 — без вектора кластер просто не участвует в сравнении
            log.info("Голос отрезка не посчитан: %s", e)
            return None
    return embed


def start_job(files, meeting, prompter_cfg, mirror=None, speaker=None, on_progress=None):
    """Модели (скачать при необходимости) + отдельный процесс. -> DiarizationJob."""
    cfg = config(prompter_cfg)
    seg, emb = models.ensure(mirror=mirror, speaker=speaker)
    exact, cap = pipeline.expected_speakers(meeting, prompter_cfg, single_track=not files.dual())
    # Предел числа голосов применяется в ядре (pipeline.limit_speakers, по векторам голоса), а не повторной
    # кластеризацией в процессе: та склеивала похожие голоса. Без модели голоса в ядре — предел в процессе.
    return runner.start(files.source(), seg, emb, num_speakers=exact, max_speakers=0 if speaker is not None else cap,
                        threshold=cfg["threshold"], num_threads=cfg["num_threads"],
                        log_path=files.log, on_progress=on_progress)


def assemble(files, result, segs_by_track, meeting, prompter_cfg, speaker=None, is_echo=None):
    """Результат процесса + куски распознавания -> (реплики, участники, интервалы); правки применены и перенесены."""
    cfg = config(prompter_cfg)
    turns = turns_from_dicts((result or {}).get("turns"))
    exact, cap = pipeline.expected_speakers(meeting, prompter_cfg, single_track=not files.dual())
    auto = not exact and not cap
    embed = wav_embedder(files.source(), speaker) if speaker is not None else None
    if files.dual():
        lines, speakers, final = pipeline.build_dual(segs_by_track.get("me") or [], segs_by_track.get("them") or [],
                                                     turns, embed=embed, auto=auto, merge_threshold=cfg["merge_threshold"],
                                                     cap=0 if exact else cap)
        if is_echo is not None:
            them = [x for x in lines if x["who"] == "them"]
            lines = [x for x in lines if x["who"] != "me" or not is_echo(x["text"], x["t"], them)]
    else:
        owner = getattr(speaker, "owner", None) if speaker is not None else None
        from speaker import SAME
        lines, speakers, final = pipeline.build_single(segs_by_track.get("mix") or [], turns, embed=embed, owner=owner,
                                                       owner_threshold=SAME, auto=auto,
                                                       merge_threshold=cfg["merge_threshold"], cap=0 if exact else cap)
    old = storage.read_rttm(files.rttm)
    ed = edits_mod.load(files.speakers)
    if old and (ed["names"] or ed["merged"] or ed["reassign"]):
        ed = edits_mod.transfer(ed, old, final)
        edits_mod.save(files.speakers, ed)
    storage.write_rttm(files.rttm, final, uri=files.stem)
    machine = [dict(s) for s in speakers]
    for x in lines:                                  # машинный результат остаётся в реплике рядом с правкой
        x["speaker0"], x["label0"], x["who0"] = x.get("speaker"), x.get("label"), x.get("who")
    lines, speakers = edits_mod.apply(lines, speakers, ed)
    return lines, speakers, final, machine


def meta(result, extra=None):
    eng = dict((result or {}).get("engine") or {})
    out = {"status": "ok", "engine": eng.get("name", "sherpa-onnx"), "version": eng.get("version", ""),
           "segmentation": eng.get("segmentation", ""), "embedding": eng.get("embedding", ""),
           "threshold": eng.get("threshold"), "num_clusters": eng.get("num_clusters", 0),
           "elapsed_sec": (result or {}).get("elapsed")}
    out.update(extra or {})
    return out


# ---------- файлы прошлой встречи ----------

def read_lines_file(files):
    try:
        data = json.loads(files.lines.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"version": 1, "lines": data}
    except (OSError, ValueError):
        return {}


def write_lines_file(files, data):
    tmp = files.lines.with_name(files.lines.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(files.lines)


TXT_LINE = re.compile(r"^\[(\d+:\d\d)\]\s*([^:]+):\s*(.*)$")


def _stamp(t):
    sec = max(0, int(float(t or 0)))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def render_body(lines):
    return [f"[{_stamp(x.get('t'))}] {line_label(x)}: {str(x.get('text') or '').strip()}"
            for x in sorted(lines, key=lambda x: float(x.get("t") or 0))]


def _split_txt(path):
    head, body = [], []
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            (body if (body or TXT_LINE.match(ln)) else head).append(ln)
    except OSError:
        pass
    return head, body


def _norm(rows):
    return [r.strip() for r in rows if r.strip()]


def _relabel_body(body, previous, lines):
    """Ручные правки текста целы: меняются только подписи строк, у которых сменился говорящий.
    Строка ищется по таймкоду и прежней подписи. -> новое тело или None (структура реплик другая)."""
    old = sorted(previous, key=lambda x: float(x.get("t") or 0))
    new = sorted(lines, key=lambda x: float(x.get("t") or 0))
    if len(old) != len(new) or any(_stamp(a.get("t")) != _stamp(b.get("t")) for a, b in zip(old, new)):
        return None
    body, used = list(body), set()
    for a, b in zip(old, new):
        was, now = line_label(a), line_label(b)
        if was == now:
            continue
        stamp = _stamp(a.get("t"))
        for k, ln in enumerate(body):
            m = TXT_LINE.match(ln)
            if k not in used and m and m.group(1) == stamp and m.group(2).strip() == was:
                body[k] = f"[{stamp}] {now}: {m.group(3)}"
                used.add(k)
                break
    return body


def rewrite_txt(files, lines, signature=None, previous=None):
    """Тело .txt — по репликам; шапка («# …») сохраняется, «# Подписи» обновляется.

    previous — реплики, по которым .txt собран в прошлый раз. Если текст с тех пор правили вручную (редактор студии),
    он не затирается: при той же структуре реплик меняются только подписи, иначе новая разметка пишется рядом,
    в <имя>.участники.txt. -> {"manual": bool, "alt": путь | ""}."""
    head, body = _split_txt(files.txt)
    head = [h for h in head if not (signature and h.startswith("# Подписи:"))]
    while head and not head[-1].strip():
        head.pop()
    if signature:
        head.append(f"# Подписи: {signature}")
    manual = previous is not None and _norm(body) != _norm(render_body(previous))
    alt = ""
    if not manual:
        out_body = render_body(lines)
    else:
        out_body = _relabel_body(body, previous, lines)
        if out_body is None:                               # пересчёт: другие реплики — рядом, ваш текст цел
            out_body = body
            alt_path = files.txt.with_suffix(".участники.txt")
            alt_path.write_text("\n".join(head) + "\n\n" + "\n".join(render_body(lines)) + "\n", encoding="utf-8")
            alt = str(alt_path)
    files.txt.parent.mkdir(parents=True, exist_ok=True)
    tmp = files.txt.with_name(files.txt.name + ".tmp")
    tmp.write_text("\n".join(head) + "\n\n" + "\n".join(out_body) + ("\n" if out_body else ""), encoding="utf-8")
    tmp.replace(files.txt)
    return {"manual": manual, "alt": alt}


SIGNATURE = "«Вы» — ваш голос, «Участник N» — разные голоса собеседников (можно переименовать в проигрывателе встречи)"


def segs_from_lines(lines, who):
    """Старая встреча без .words.json и без распознавания: куски — из готовых реплик (без времени слов)."""
    rows = [x for x in lines if x.get("who") in who and str(x.get("text") or "").strip()]
    rows.sort(key=lambda x: float(x.get("t") or 0))
    out = []
    for i, x in enumerate(rows):
        t = float(x.get("t") or 0)
        end = float(x.get("end") or 0) or (float(rows[i + 1]["t"]) if i + 1 < len(rows) else t + 5.0)
        out.append((t, max(end, t + 0.5), str(x["text"]).strip(), None))
    return out


def rebuild(meetings_root, summary_rel, prompter_cfg, *, speaker=None, mirror=None, transcribe=None,
            is_echo=None, on_progress=None, timeout=None):
    """«Разбить по участникам» / «Пересчитать» для прошлой встречи. transcribe(path) -> куски со словами
    (нужен, если нет .words.json). -> {"speakers", "lines", "words": bool}."""
    files = MeetingFiles.from_summary(meetings_root, summary_rel)
    if not files.ok(files.source()):
        raise ValueError("у этой встречи нет аудиозаписи — разбить по участникам нельзя")
    data = read_lines_file(files)
    old_lines = list(data.get("lines") or [])
    m = dict(data.get("meeting") or {})
    if prompter_cfg and prompter_cfg.get("expected_speakers_override") is not None:
        m["expected_speakers"] = prompter_cfg["expected_speakers_override"]
    seconds = wav_seconds(files.source())
    cfg = config(prompter_cfg)
    if seconds < cfg["min_duration_sec"]:
        raise ValueError("запись слишком короткая для разбиения по участникам")

    def prog(stage, value):
        if on_progress:
            on_progress(stage, value)

    job = start_job(files, m, prompter_cfg, mirror=mirror, speaker=speaker,
                    on_progress=lambda p: prog("diarize", p))
    segs = storage.read_words(files.words)
    need = ("me", "them") if files.dual() else ("mix",)
    if not all(k in segs for k in need):
        if transcribe is not None:
            segs = {}
            for k in need:
                prog("transcribe", 0.0)
                segs[k] = transcribe(files.me if k == "me" else files.them if k == "them" else files.mix)
            storage.write_words(files.words, segs)
        else:
            segs = ({"me": segs_from_lines(old_lines, ("me",)), "them": segs_from_lines(old_lines, ("them",))}
                    if files.dual() else {"mix": segs_from_lines(old_lines, ("me", "them"))})
    result = job.wait(timeout if timeout is not None else seconds * cfg["timeout_factor"] + 120)
    if not result:
        raise RuntimeError(job.error or "разбиение по участникам не получилось")
    prog("align", 1.0)
    lines, speakers, _turns, machine = assemble(files, result, segs, m, prompter_cfg, speaker=speaker, is_echo=is_echo)
    screens = [x for x in old_lines if x.get("who") == "screen"]
    lines = sorted(lines + screens, key=lambda x: float(x.get("t") or 0))
    data.update({"version": 2, "speaker_mode": "participants", "speakers": speakers, "speakers_machine": machine,
                 "lines": lines,
                 "diarization": meta(result, {"words": storage.has_words(segs)})})
    data.setdefault("meeting", m)
    write_lines_file(files, data)
    txt = rewrite_txt(files, lines, SIGNATURE, previous=old_lines)
    return {"speakers": speakers, "lines": len(lines), "words": storage.has_words(segs), "txt": txt}


def edit(meetings_root, summary_rel, action, **kw):
    """Правка участников прошлой встречи: rename(id, name) | merge(src, dst) | reassign(t, end, to).
    Пишется только .speakers.json; .lines.json и .txt пересобираются из машинного результата + правок."""
    files = MeetingFiles.from_summary(meetings_root, summary_rel)
    data = read_lines_file(files)
    if not data.get("speakers"):
        raise ValueError("встреча ещё не разбита по участникам")
    ed = edits_mod.load(files.speakers)
    if action == "rename":
        edits_mod.rename(ed, kw.get("id"), kw.get("name"))
    elif action == "merge":
        edits_mod.merge(ed, kw.get("id"), kw.get("to"))
    elif action == "reassign":
        t, end = float(kw.get("t") or 0), kw.get("end")
        candidates = [x for x in data.get("lines") or [] if x.get("who") != "screen"
                      and abs(float(x.get("t") or 0) - t) < 0.15]
        source = kw.get("source")
        if source is not None:
            candidates = [x for x in candidates if str(x.get("speaker0") or x.get("speaker")) == str(source)]
        if len(candidates) != 1:
            raise ValueError("Не удалось однозначно выбрать реплику; обновите проигрыватель.")
        line = candidates[0]
        if line is not None and float(line.get("end") or 0) > t:
            end = float(line["end"])                       # конец реплики — для аудио участников
        source = str(line.get("speaker0") or line.get("speaker") or "")
        track = ("me" if (line.get("who0") or line.get("who")) == "me" else "them") if files.dual() else "mix"
        edits_mod.reassign(ed, t, end, kw.get("to"), source=source, track=track)
    else:
        raise ValueError("правка участников: rename, merge или reassign")
    edits_mod.save(files.speakers, ed)
    previous = list(data.get("lines") or [])
    lines, speakers = apply_saved(data, ed)
    data["lines"], data["speakers"] = lines, speakers
    write_lines_file(files, data)
    txt = rewrite_txt(files, lines, SIGNATURE, previous=previous)
    return {"speakers": speakers, "edits": ed, "txt": txt}


def apply_saved(data, ed):
    """Правки поверх машинного результата из .lines.json (speaker0/label0/who0, speakers_machine)."""
    machine = data.get("speakers_machine") or [{k: v for k, v in s.items() if k not in ("display", "name")}
                                               for s in data.get("speakers") or []]
    base = []
    for x in data.get("lines") or []:
        row = {k: v for k, v in x.items() if k != "edited"}
        if "speaker0" in x:
            row["speaker"], row["label"], row["who"] = x.get("speaker0"), x.get("label0"), x.get("who0") or x.get("who")
        base.append(row)
    return edits_mod.apply(base, machine, ed)


def export_audio(meetings_root, summary_rel, mode="timeline"):
    """WAV каждого участника с учётом объединений и переназначений.
    Онлайн: «Вы» — микрофон (без отрезков, отданных другим) + отрезки дорожки собеседника, отданные вам;
    участник — свои отрезки дорожки собеседника + отданные ему отрезки микрофона. Одна дорожка — всё из неё."""
    files = MeetingFiles.from_summary(meetings_root, summary_rel)
    data = read_lines_file(files)
    speakers = data.get("speakers") or []
    turns = storage.read_rttm(files.rttm)
    if not speakers or not turns:
        raise ValueError("встреча ещё не разбита по участникам")
    ed = edits_mod.load(files.speakers)
    mode = mode if mode in ("timeline", "speech_only") else "timeline"
    src = files.them if files.dual() else files.mix
    sr, n = _wav_info(src)
    # Старые правки без источника переносим только при единственной подходящей реплике.
    for edit in ed["reassign"]:
        if not edit.get("source") or not edit.get("track"):
            matches = [x for x in data.get("lines") or [] if x.get("who") != "screen"
                       and abs(float(x.get("t") or 0) - float(edit.get("t") or 0)) < 0.15]
            if len(matches) == 1:
                row = matches[0]
                edit["source"] = str(row.get("speaker0") or row.get("speaker") or "")
                edit["track"] = ("me" if (row.get("who0") or row.get("who")) == "me" else "them") if files.dual() else "mix"
            else:
                raise ValueError("Не удалось определить исходную реплику старой правки; назначьте говорящего заново.")
    remote = edits_mod.apply_audio(turns, ed, track="them" if files.dual() else "mix")
    mic = files.me if files.dual() and files.ok(files.me) else None
    mic_attr = []
    if mic:
        own = [Turn(float(x.get("t") or 0), float(x.get("end") or 0), "ME") for x in data.get("lines") or []
               if (x.get("speaker0") or x.get("speaker")) == "ME" and float(x.get("end") or 0) > float(x.get("t") or 0)]
        mic_attr = edits_mod.apply_audio(own, ed, track="me")
    plan = []
    for s in speakers:
        sid = s["id"]
        sources = []
        mine = [(t.start, t.end) for t in remote if t.speaker == sid]
        if mine:
            sources.append((src, tracks.frame_spans(mine, sr, n)))
        if mic:
            if sid == "ME":
                away = [(t.start, t.end) for t in mic_attr if t.speaker != "ME"]
                if mode == "timeline":
                    keep = tracks.complement(tracks.frame_spans(away, sr, n, pad=0.0), n)
                else:
                    keep = tracks.frame_spans([(t.start, t.end) for t in mic_attr if t.speaker == "ME"], sr, n)
                sources.append((mic, keep))
            else:
                given = [(t.start, t.end) for t in mic_attr if t.speaker == sid]
                if given:
                    sources.append((mic, tracks.frame_spans(given, sr, n)))
        plan.append({"id": sid, "label": s.get("display") or s.get("label") or sid, "sources": sources, "length": n})
    return tracks.export_plan(files.folder, plan, mode=mode)


def _wav_info(path):
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), w.getnframes()


def speakers_view(meetings_root, summary_rel):
    files = MeetingFiles.from_summary(meetings_root, summary_rel)
    data = read_lines_file(files)
    ed = edits_mod.load(files.speakers)
    st = models.status()
    return {"speakers": data.get("speakers") or [], "mode": data.get("speaker_mode") or "",
            "diarization": data.get("diarization") or {}, "not_transferred": ed.get("not_transferred") or [],
            "can_diarize": files.ok(files.source()), "words": files.words.is_file(), "models": st,
            "audio": _audio_list(files)}


def _audio_list(files):
    try:
        meta_ = json.loads((files.folder / "Участники" / "участники.json").read_text(encoding="utf-8"))
        return [x for x in meta_.get("files") or [] if (files.folder / "Участники" / str(x.get("file"))).is_file()]
    except (OSError, ValueError, AttributeError):
        return []


def samples_rms(pcm):
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if len(a) else 0.0
