"""Стенд разбиения встречи по участникам (plan/meeting-diarization-plan-2026-11-06.md, этап 1).

Два режима, оба без токенов и без Hugging Face:

1) Синтетическая русская встреча (проверка всего пути Dewiar: распознавание со временем слов → отдельный процесс
   sherpa-onnx → выравнивание → «Вы» по профилю голоса):
     python -B tools/diar_bench.py synthetic --models ~/diar-models
   В папке моделей (всё — релизы k2-fsa/sherpa-onnx на GitHub):
     sherpa-onnx-pyannote-segmentation-3-0/model.onnx
     3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx
     vits-piper-ru_RU-{dmitri,irina,denis,ruslan}-medium-int8/   (tts-models, голоса Piper; нужен pip piper-tts)
     sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16/   (asr-models, GigaAM v3 с пунктуацией)
   GigaAM здесь подключён через sherpa-onnx с тем же интерфейсом, что onnx-asr (recognize / with_timestamps):
   в стенде нет кэша Hugging Face, а код Dewiar (stt.tokens_to_words, pipeline, speaker) — настоящий.

2) Свои записи с эталоном (DER, число говорящих, время):
     python -B tools/diar_bench.py der --models ~/diar-models --wav встреча.wav --rttm эталон.rttm [--threshold 0.4:0.8:0.1]
   Эталон — RTTM (можно из меток Audacity: начало, конец, имя — скрипт ниже, --labels).
   DER считается по кадрам 10 мс без «коллара», сопоставление меток — жадное по наибольшему перекрытию
   (оценка сверху для перестановки Венгерского алгоритма; для сравнения порогов этого достаточно).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import numpy as np  # noqa: E402

RATE = 16000


# ---------- модели и окружение ----------

def setup_state(models):
    """Отдельный STATE_DIR: модель голоса для speaker.py — копия из папки стенда; рабочие данные Dewiar не трогаем."""
    state = Path(tempfile.mkdtemp(prefix="dewiar-diar-bench-"))
    os.environ["DEWIAR_STATE_DIR"] = str(state)
    from speaker import MODEL_NAME
    (state / "models" / "speaker").mkdir(parents=True)
    shutil.copyfile(Path(models) / MODEL_NAME, state / "models" / "speaker" / MODEL_NAME)
    return state


def paths(models):
    from speaker import MODEL_NAME
    m = Path(models)
    return m / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx", m / MODEL_NAME


def worker_cmd():
    return [sys.executable, "-B", str(ROOT / "core" / "diarization" / "worker.py")]


def read_wav(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav(path, a):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.clip(a, -32768, 32767).astype(np.int16).tobytes())


# ---------- распознавание (интерфейс onnx-asr поверх sherpa-onnx) ----------

class _Ts:
    def __init__(self, owner):
        self.owner = owner

    def recognize(self, a, sample_rate=RATE):
        return self.owner.run(a, sample_rate)


class GigaAM:
    def __init__(self, models):
        import sherpa_onnx
        d = Path(models) / "sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16"
        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.onnx"), joiner=str(d / "joiner.onnx"),
            tokens=str(d / "tokens.txt"), model_type="nemo_transducer", num_threads=4)

    def run(self, a, sr):
        from types import SimpleNamespace
        s = self.rec.create_stream()
        s.accept_waveform(sr, np.asarray(a, dtype=np.float32))
        self.rec.decode_stream(s)
        r = s.result
        return SimpleNamespace(text=r.text.strip(), tokens=[t.replace("▁", " ") for t in r.tokens],
                               timestamps=list(r.timestamps))

    def with_timestamps(self):
        return _Ts(self)


def transcribe(path, asr, chunk=22.0):
    """Как engine_brain.transcribe_file(words=True): куски до 22 с по самому тихому месту, слова от начала файла."""
    from stt import tokens_to_words
    a = read_wav(path)
    n, win, out, start = len(a), 480, [], 0
    sq = np.concatenate(([0.0], np.cumsum(a.astype(np.float64) ** 2)))
    while start < n:
        end = min(n, start + int(chunk * RATE))
        if end < n:
            lo = max(start + 8 * RATE, end - 6 * RATE)
            idx = np.arange(lo, end)
            e = sq[np.minimum(idx + win // 2, n)] - sq[np.maximum(idx - win // 2, 0)]
            end = lo + int(np.argmin(e))
        pcm = a[start:end]
        if len(pcm) > RATE // 4 and np.abs(pcm).max() > 300:
            r = asr.with_timestamps().recognize(pcm.astype(np.float32) / 32768.0)
            ws = [dict(w, s=round(w["s"] + start / RATE, 2), e=round(w["e"] + start / RATE, 2))
                  for w in tokens_to_words(r.tokens, r.timestamps)]
            if r.text:
                out.append((start / RATE, end / RATE, r.text, ws or None))
        start = end
    return out


# ---------- синтетическая встреча ----------

SCRIPT = [
    ("dmitri", "Добрый день, коллеги. Давайте начнём совещание по поставке шкафов на склад."),
    ("irina", "Здравствуйте. У нас по графику первая партия приходит в понедельник."),
    ("denis", "Я проверил документы, счёт оплачен, но транспортная компания ещё не подтвердила машину."),
    ("dmitri", "Хорошо. Денис, уточните, пожалуйста, у перевозчика до завтра."),
    ("denis", "Да, сделаю до обеда."),
    ("ruslan", "Со стороны монтажа всё готово, бригада свободна со вторника."),
    ("irina", "Тогда предлагаю монтаж начинать в среду, чтобы был запас на приёмку."),
    ("dmitri", "Согласен. Руслан, подготовьте список инструментов и пропуска для бригады."),
    ("ruslan", "Принято, пришлю список сегодня вечером."),
    ("irina", "И ещё вопрос по цене: поставщик просит доплату за срочность, около пяти процентов."),
    ("denis", "Я бы попросил скидку, мы работаем с ними третий год."),
    ("dmitri", "Ирина, проведите переговоры, наш предел — три процента."),
    ("irina", "Хорошо, договорюсь и напишу вам в четверг."),
    ("ruslan", "Последнее: нужно согласовать место хранения до начала монтажа."),
    ("dmitri", "Согласуем на складе в Подольске. На этом всё, спасибо всем."),
]
ENROLL = ("Запомни мой голос, пожалуйста.", "Сегодня хорошая погода для работы.",
          "Я проверяю запись своего голоса.", "Один, два, три, четыре, пять.")


class Voices:
    def __init__(self, models):
        self.models = Path(models)
        self.cache = {}

    def say(self, name, text):
        import io
        from piper import PiperVoice
        if name not in self.cache:
            self.cache[name] = PiperVoice.load(str(self.models / f"vits-piper-ru_RU-{name}-medium-int8" / f"ru_RU-{name}-medium.onnx"))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self.cache[name].synthesize_wav(text, w)
        buf.seek(0)
        with wave.open(buf) as w:
            sr = w.getframerate()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        n = int(len(a) * RATE / sr)
        return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)


def render(voices, script, seed=3, gap=0.6):
    rng = np.random.default_rng(seed)
    t, parts, ref = 0.5, [], []
    for name, text in script:
        a = voices.say(name, text)
        parts.append((name, int(t * RATE), a))
        ref.append((name, t, t + len(a) / RATE, text))
        t += len(a) / RATE + gap + float(rng.uniform(-0.2, 0.3))
    n = int((t + 0.5) * RATE)
    tracks = {}
    for name, start, a in parts:
        tracks.setdefault(name, np.zeros(n, np.float32))[start:start + len(a)] += a
    return tracks, ref


def majority(lines, ref):
    votes = {}
    for name, s, e, _ in ref:
        for x in lines:
            ov = min(e, float(x.get("end") or x["t"])) - max(s, x["t"])
            if ov > 0:
                votes.setdefault(x.get("speaker"), {}).setdefault(name, 0.0)
                votes[x["speaker"]][name] += ov
    return {k: max(v, key=v.get) for k, v in votes.items()}


def wder(lines, ref, voice_of):
    """Доля слов эталона у реплики, которая перекрывает его больше всех и подписана не тем голосом."""
    wrong = total = 0
    bad = []
    for name, s, e, text in ref:
        n = len(text.split())
        total += n
        hits = [x for x in lines if x["t"] <= e and float(x.get("end") or x["t"]) >= s]
        best = max(hits, key=lambda x: min(e, float(x.get("end") or x["t"])) - max(s, x["t"])) if hits else None
        if best is None or voice_of.get(best.get("speaker")) != name:
            wrong += n
            bad.append((round(s, 1), name, text[:40]))
    return wrong / max(total, 1), bad


def synthetic(args):
    setup_state(args.models)
    from diarization import pipeline, runner, service
    from speaker import Speaker
    seg, emb = paths(args.models)
    voices, asr = Voices(args.models), GigaAM(args.models)
    tracks, ref = render(voices, SCRIPT, seed=args.seed)
    owner_voice = "dmitri"
    root = Path(tempfile.mkdtemp(prefix="dewiar-diar-meeting-")) / "Встречи"
    (root / "Аудио" / "M").mkdir(parents=True)
    (root / "Расшифровки").mkdir()
    me = tracks[owner_voice]
    them = sum(v for k, v in tracks.items() if k != owner_voice)
    write_wav(root / "Аудио" / "M" / "Вы.wav", me)
    write_wav(root / "Аудио" / "M" / "Собеседник.wav", them)
    write_wav(root / "Аудио" / "M.wav", me + them)
    print(f"Синтетическая встреча: {len(me) / RATE:.0f} с, реплик {len(ref)}, голосов {len(tracks)}")
    sp = Speaker()
    vecs = [sp.embed(voices.say(owner_voice, t).astype(np.int16).tobytes()) for t in ENROLL]
    c = np.mean([v for v in vecs if v is not None], 0)
    owner = c / np.linalg.norm(c)
    report, failed = {}, []
    for case in ("dual", "single", "single_hint"):
        if case == "dual":
            files = service.MeetingFiles(root, "M")
        else:
            files = service.MeetingFiles.explicit(root / "Аудио" / "M.wav", root / "Расшифровки" / f"{case}.txt")
        meeting = {"place": "online" if case == "dual" else "live",
                   "participants": "Вы, Ирина, Денис, Руслан" if case == "single_hint" else ""}
        exact, cap = pipeline.expected_speakers(meeting, {}, single_track=not files.dual())
        t0 = time.time()
        # предел голосов (cap) применяет ядро по векторам голоса — как в service.start_job/assemble
        job = runner.start(files.source(), seg, emb, num_speakers=exact, max_speakers=0, threshold=args.threshold,
                           cmd=worker_cmd())
        segs = ({"me": transcribe(files.me, asr), "them": transcribe(files.them, asr)} if case == "dual"
                else {"mix": transcribe(files.mix, asr)})
        t_asr = time.time() - t0
        result = job.wait(600)
        if not result:
            raise SystemExit(f"[{case}] процесс разбиения: {job.error}")
        sp.owner = None if case == "dual" else owner
        lines, speakers, _final, _m = service.assemble(files, result, segs, meeting, {"diarization": {"threshold": args.threshold}},
                                                       speaker=sp)
        voice = majority(lines, ref)
        voice["ME"] = owner_voice
        w, bad = wder(lines, ref, voice)
        has_me = any(s["id"] == "ME" for s in speakers)
        report[case] = {"wder": round(w, 3), "engine_speakers": result["speakers"], "participants": len(speakers),
                        "owner_found": has_me, "asr_sec": round(t_asr, 1), "diarize_sec": result["elapsed"],
                        "errors": bad}
        print(f"[{case}] WDER {w:.1%} · голосов у движка {result['speakers']} (точно={exact}, предел={cap}) · "
              f"«Вы» {'найден' if has_me else 'НЕ найден'} · распознавание {t_asr:.1f} с, разбиение {result['elapsed']} с")
        for x in lines:
            print(f"    [{x['t']:6.1f}] {x['label']}: {x['text'][:70]}")
        if w > args.max_wder or not has_me:
            failed.append(case)
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if failed:
        raise SystemExit(f"НЕ ПРОЙДЕНО: {failed} (WDER > {args.max_wder:.0%} или «Вы» не найден)")
    print("ПРОЙДЕНО:", {k: f"{v['wder']:.1%}" for k, v in report.items()})


# ---------- DER по своим записям ----------

def read_ref(path, labels=False):
    from diarization.storage import read_rttm
    from diarization.schema import Turn
    if not labels:
        return read_rttm(path)
    out = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():   # метки Audacity: начало\tконец\tимя
        parts = ln.split("\t")
        if len(parts) >= 3:
            out.append(Turn(float(parts[0]), float(parts[1]), parts[2].strip() or "?"))
    return out


def der(ref, hyp, duration, step=0.01):
    n = int(duration / step) + 1
    rs = sorted({t.speaker for t in ref})
    hs = sorted({t.speaker for t in hyp})
    R = np.zeros((n, len(rs)), bool)
    H = np.zeros((n, len(hs)), bool)
    for t in ref:
        R[int(t.start / step):int(t.end / step), rs.index(t.speaker)] = True
    for t in hyp:
        H[int(t.start / step):int(t.end / step), hs.index(t.speaker)] = True
    ov = R.T.astype(np.int32) @ H.astype(np.int32)
    mapping, used = {}, set()
    for i, j in sorted(np.ndindex(ov.shape), key=lambda ij: -ov[ij]):
        if i not in mapping and j not in used and ov[i, j] > 0:
            mapping[i] = j
            used.add(j)
    nr, nh = R.sum(1), H.sum(1)
    correct = np.zeros(n, np.int32)
    for i, j in mapping.items():
        correct += (R[:, i] & H[:, j])
    total = nr.sum()
    miss = np.maximum(nr - nh, 0).sum()
    fa = np.maximum(nh - nr, 0).sum()
    conf = (np.minimum(nr, nh) - correct).sum()
    return {"der": round(float((miss + fa + conf) / max(total, 1)), 4), "miss": round(float(miss / max(total, 1)), 4),
            "false_alarm": round(float(fa / max(total, 1)), 4), "confusion": round(float(conf / max(total, 1)), 4),
            "ref_speakers": len(rs), "hyp_speakers": len(hs)}


def der_mode(args):
    setup_state(args.models)
    from diarization import runner
    seg, emb = paths(args.models)
    ref = read_ref(args.rttm, args.labels)
    duration = len(read_wav(args.wav)) / RATE
    lo, hi, st = (float(x) for x in (args.threshold_range or f"{args.threshold}:{args.threshold}:1").split(":"))
    rows = []
    th = lo
    while th <= hi + 1e-9:
        job = runner.start(args.wav, seg, emb, num_speakers=args.num_speakers, threshold=th, cmd=worker_cmd())
        r = job.wait(3600)
        if not r:
            raise SystemExit(job.error)
        from diarization.schema import turns_from_dicts
        m = der(ref, turns_from_dicts(r["turns"]), duration)
        m.update(threshold=round(th, 3), rtf=round(r["elapsed"] / max(duration, 1e-3), 4))
        rows.append(m)
        print(json.dumps(m, ensure_ascii=False))
        th += st
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("synthetic")
    a.add_argument("--models", required=True)
    a.add_argument("--seed", type=int, default=3)
    a.add_argument("--threshold", type=float, default=0.4)
    a.add_argument("--max-wder", type=float, default=0.15)
    a.add_argument("--out")
    b = sub.add_parser("der")
    b.add_argument("--models", required=True)
    b.add_argument("--wav", required=True)
    b.add_argument("--rttm", required=True)
    b.add_argument("--labels", action="store_true", help="эталон — метки Audacity (начало, конец, имя)")
    b.add_argument("--num-speakers", type=int, default=0)
    b.add_argument("--threshold", type=float, default=0.4)
    b.add_argument("--threshold-range", help="от:до:шаг, например 0.4:0.8:0.1")
    b.add_argument("--out")
    args = ap.parse_args(argv)
    (synthetic if args.mode == "synthetic" else der_mode)(args)


if __name__ == "__main__":
    main()
