"""Аудио каждого участника: Аудио/<имя>/Участники/<подпись>.wav + участники.json.

Это разметка по времени, а не изоляция голоса: участок одновременной речи попадает в дорожки обоих.
timeline — длительность как у исходника, вне реплик тишина; speech_only — только речь, паузы 0,3 с, карта времени.

Дорожка участника собирается из нескольких источников: «Вы» в онлайн-встрече — микрофон целиком плюс отрезки
дорожки собеседника, объединённые или переназначенные на вас; минус отрезки микрофона, переназначенные другому.
Участник — свои отрезки дорожки собеседника (или общей) плюс переназначенные ему отрезки микрофона.

Новый комплект пишется во временную папку и заменяет прежний только после успешной записи всех файлов.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import wave
from pathlib import Path

import numpy as np

CHUNK_SEC = 10.0


def _merge(spans):
    out = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def frame_spans(intervals, sr, n, pad=0.15):
    """[(начало_с, конец_с)] -> слитые отрезки в отсчётах с полями pad."""
    return _merge([(max(0, int((a - pad) * sr)), min(n, int((b + pad) * sr))) for a, b in intervals])


def complement(spans, n):
    """Всё, кроме spans, внутри [0, n)."""
    out, pos = [], 0
    for a, b in _merge(spans):
        if a > pos:
            out.append([pos, a])
        pos = max(pos, b)
    if pos < n:
        out.append([pos, n])
    return out


def _env(x0, length, spans, fade_n):
    """Огибающая источника на блоке [x0, x0+length): 1 внутри отрезков, плавные края fade_n отсчётов."""
    env = np.zeros(length, dtype=np.float32)
    x1 = x0 + length
    for a, b in spans:
        if b <= x0 or a >= x1:
            continue
        lo, hi = max(a, x0), min(b, x1)
        idx = np.arange(lo, hi, dtype=np.float64)
        f = max(1, min(fade_n, (b - a) // 2))
        v = np.clip(np.minimum((idx - a + 1) / f, (b - idx) / f), 0.0, 1.0).astype(np.float32)
        env[lo - x0:hi - x0] = np.maximum(env[lo - x0:hi - x0], v)
    return env


class _Source:
    def __init__(self, path, spans):
        self.w = wave.open(str(path), "rb")
        if self.w.getsampwidth() != 2 or self.w.getnchannels() != 1:
            self.w.close()
            raise ValueError("ожидается WAV PCM 16 бит, моно")
        self.sr, self.n, self.spans = self.w.getframerate(), self.w.getnframes(), spans

    def read(self, x0, length):
        out = np.zeros(length, dtype=np.float32)
        if x0 >= self.n:
            return out
        self.w.setpos(x0)
        data = np.frombuffer(self.w.readframes(min(length, self.n - x0)), dtype=np.int16).astype(np.float32)
        out[:len(data)] = data
        return out

    def close(self):
        self.w.close()


def mix_track(sources, dst, *, mode="timeline", fade=0.015, gap=0.3, length=None):
    """sources — [(путь WAV, отрезки в отсчётах)]; все одной частоты. Пишет dst через .part.
    -> {"file", "mode", "timemap"}."""
    srcs = []
    try:
        for p, s in sources:
            if p:
                srcs.append(_Source(p, s))
        if not srcs:
            raise ValueError("нет источников звука")
        sr = srcs[0].sr
        if any(s.sr != sr for s in srcs):
            raise ValueError("дорожки встречи разной частоты")
        n = int(length or max(s.n for s in srcs))
        fade_n = int(fade * sr)
        step = int(CHUNK_SEC * sr)
        tmp = Path(dst).with_name(Path(dst).name + ".part")
        timemap = []
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)

            def block(x0, ln):
                acc = np.zeros(ln, dtype=np.float32)
                for s in srcs:
                    env = _env(x0, ln, s.spans, fade_n)
                    if env.any():
                        acc += s.read(x0, ln) * env
                return np.clip(acc, -32768, 32767).astype(np.int16).tobytes()

            if mode == "speech_only":
                out_pos = 0
                for a, b in _merge([sp for s in srcs for sp in s.spans]):
                    if timemap:
                        w.writeframes(b"\0\0" * int(gap * sr))
                        out_pos += int(gap * sr)
                    for x0 in range(a, b, step):
                        w.writeframes(block(x0, min(step, b - x0)))
                    timemap.append({"out": round(out_pos / sr, 3), "src": round(a / sr, 3), "dur": round((b - a) / sr, 3)})
                    out_pos += b - a
            else:
                for x0 in range(0, n, step):
                    w.writeframes(block(x0, min(step, n - x0)))
        tmp.replace(dst)
        return {"file": Path(dst).name, "mode": mode, "timemap": timemap if mode == "speech_only" else None}
    finally:
        for s in srcs:
            s.close()


def export_speaker_track(src, turns, dst, *, mode="timeline", pad=0.15, fade=0.015, gap=0.3):
    """Один источник: интервалы turns из src."""
    with wave.open(str(src), "rb") as r:
        sr, n = r.getframerate(), r.getnframes()
    spans = frame_spans([(t.start, t.end) for t in turns], sr, n, pad)
    return mix_track([(src, spans)], dst, mode=mode, fade=fade, gap=gap, length=n)


def safe_name(label, used):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", str(label or "")).strip()[:60] or "Участник"
    name, n = base, 2
    while name.lower() in used:
        name = f"{base} ({n})"
        n += 1
    used.add(name.lower())
    return name


def export_plan(folder, plan, *, mode="timeline"):
    """folder — Аудио/<имя>; plan — [{"id", "label", "sources": [(путь, отрезки в отсчётах)], "length"?}].
    Новый комплект пишется в Участники.new-<pid> и заменяет прежний только после успеха. -> {"folder", "files"}."""
    folder = Path(folder)
    final = folder / "Участники"
    work = folder / f"Участники.new-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    used, files, meta = set(), [], {"mode": mode, "files": []}
    try:
        for item in plan:
            sources = [(p, s) for p, s in item["sources"] if p and s]
            if not sources:
                continue
            name = safe_name(item["label"], used) + ".wav"
            info = mix_track(sources, work / name, mode=mode, length=item.get("length"))
            files.append({"id": item["id"], "label": item["label"], "file": name})
            meta["files"].append(dict(info, id=item["id"], label=item["label"]))
        (work / "участники.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)               # прежний экспорт остаётся как был
        raise
    old = folder / f"Участники.old-{os.getpid()}"
    moved = False
    try:
        if final.exists():
            final.replace(old)
            moved = True
        work.replace(final)
    except BaseException:
        if moved and not final.exists():
            old.replace(final)
        shutil.rmtree(work, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    return {"folder": str(final), "files": files}
