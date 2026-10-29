"""Звуки напоминаний телефона (android/res/raw/alarm_*.ogg) — синтез, без чужих записей и мелодий.
«Фирменный» (signature) — мотив Dewiar на FM-колоколе: его узнают с первых нот (по умолчанию); «Капли» (drops) —
мелодия из капель воды; «Орбита» (orbit) — мягкий взлёт и пузырьки с эхом. Каждый — короткая мягкая фраза с «воздухом» (искусственный зал) и паузой в конце: AlarmActivity повторяет её по кругу,
плавно прибавляя громкость, а уведомление играет её один раз. Мотивы — свои, в пентатонике: приятно, без «подъёма на работу».
  chime  — «Колокольчики»: стеклянные колокольчики, вверх и обратно;
  marimba — «Маримба»: тёплые деревянные пластины, напев;
  harp   — «Арфа»: перебор струн и тихий аккорд;
  dawn   — «Рассвет»: мягкий аккорд нарастает, сверху — искры колокольчиков.
Запуск: python3 tools/alarm_sounds.py [папка] (по умолчанию android/res/raw). Нужны numpy, scipy и ffmpeg с libvorbis."""
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve, butter, lfilter

SR = 44100
rng = np.random.default_rng(7)


def hz(note):
    """«C5», «F#4» → частота."""
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    n, o = note[:-1], int(note[-1])
    return 440.0 * 2 ** ((names[n] + 12 * (o + 1) - 69) / 12)


def lowpass(x, f):
    b, a = butter(2, f / (SR / 2))
    return lfilter(b, a, x)


def room(x, seconds=1.8, decay=0.55, wet=0.28, bright=5200):
    """Зал: свёртка с затухающим шумом (мягкий, без металлического звона)."""
    n = int(SR * seconds)
    t = np.arange(n) / SR
    ir = lowpass(rng.standard_normal(n), bright) * np.exp(-t / decay)
    ir[: int(SR * 0.012)] = 0                                  # ранние отражения чуть позже прямого звука
    ir /= np.sqrt(np.sum(ir ** 2))
    y = fftconvolve(x, ir)[: len(x)]
    return (1 - wet) * x + wet * y * 0.9


def strike(out, start, f, partials, amp=1.0, attack=0.004):
    """Удар по колокольчику/пластине: набор обертонов (отношение частоты, громкость, время затухания)."""
    i0 = int(SR * start)
    longest = max(p[2] for p in partials) * 6
    n = min(int(SR * longest), len(out) - i0)
    if n <= 0:
        return
    t = np.arange(n) / SR
    env_a = np.minimum(1.0, t / attack)
    s = np.zeros(n)
    for ratio, a, tau in partials:
        ph = rng.uniform(0, 2 * math.pi)
        s += a * np.sin(2 * math.pi * f * ratio * t + ph) * np.exp(-t / tau)
    out[i0:i0 + n] += amp * env_a * s


def pluck(out, start, f, amp=1.0, decay=0.995, bright=0.5):
    """Струна (Карплус — Стронг): шумовой щипок в линии задержки, мягкое усреднение; без резкого верха и обрыва в конце."""
    i0 = int(SR * start)
    n = min(int(SR * 3.4), len(out) - i0)
    p = int(round(SR / f))
    buf = lowpass(rng.uniform(-1, 1, p), 1400 + 2600 * bright)
    y = np.zeros(n)
    y[:p] = buf
    for i in range(p + 1, n):
        y[i] = decay * 0.5 * (y[i - p] + y[i - p - 1])
    y = lowpass(y - np.mean(y), 4200)
    y *= np.minimum(1, np.arange(n) / (SR * 0.002))
    tail = min(n, int(SR * 0.6))
    y[-tail:] *= np.linspace(1, 0, tail) ** 2
    out[i0:i0 + n] += amp * y


def finish(x, peak_db=-4.0, fade=0.25):
    x = x - np.mean(x)
    f = int(SR * fade)
    x[-f:] *= np.linspace(1, 0, f) ** 2                        # конец — тишина: повтор по кругу без щелчка
    x[:64] *= np.linspace(0, 1, 64)
    return x / np.max(np.abs(x)) * 10 ** (peak_db / 20)


GLASS = [(1.0, 1.0, 1.4), (2.0, 0.28, 0.6), (3.0, 0.1, 0.35), (4.17, 0.05, 0.2)]
MARIMBA = [(1.0, 1.0, 0.55), (3.93, 0.22, 0.12), (9.2, 0.05, 0.04)]
BELL = [(1.0, 1.0, 1.8), (2.0, 0.35, 1.0), (2.76, 0.18, 0.7), (5.4, 0.06, 0.3)]


def chime():
    out = np.zeros(int(SR * 5.2))
    up = ["E6", "G#6", "B6", "C#7", "E7"]
    for k, nt in enumerate(up):
        strike(out, 0.05 + k * 0.17, hz(nt), GLASS, amp=0.55 + 0.08 * k)
    for k, nt in enumerate(["C#7", "B6", "G#6"]):
        strike(out, 1.15 + k * 0.2, hz(nt), GLASS, amp=0.6 - 0.08 * k)
    strike(out, 1.85, hz("E6"), GLASS, amp=0.55)
    strike(out, 1.85, hz("B6"), GLASS, amp=0.35)
    return finish(room(out, wet=0.32))


def marimba():
    out = np.zeros(int(SR * 5.0))
    beat = 0.2
    tune = [("C5", 0), ("E5", 1), ("G5", 2), ("A5", 3), ("G5", 4.5), ("E5", 5), ("D5", 6), ("C5", 8),
            ("D5", 10), ("E5", 11), ("G5", 12), ("C6", 13.5)]
    for nt, b in tune:
        f = hz(nt)
        strike(out, 0.04 + b * beat, f, MARIMBA, amp=0.8, attack=0.002)
        strike(out, 0.04 + b * beat, f / 2, [(1.0, 0.18, 0.4)], attack=0.003)     # мягкий низ — «дерево»
    strike(out, 0.04 + 13.5 * beat, hz("G5"), MARIMBA, amp=0.45, attack=0.002)
    strike(out, 0.04 + 13.5 * beat, hz("E5"), MARIMBA, amp=0.4, attack=0.002)
    return finish(room(out, wet=0.24, decay=0.45))


def harp():
    out = np.zeros(int(SR * 5.6))
    arp = ["C4", "G4", "D5", "E5", "G5", "B5", "D6", "E6"]
    for k, nt in enumerate(arp):
        pluck(out, 0.03 + k * 0.085, hz(nt), amp=0.75, bright=0.35 + 0.05 * k)
    for k, nt in enumerate(["D6", "B5", "G5"]):
        pluck(out, 1.2 + k * 0.16, hz(nt), amp=0.6, bright=0.5)
    for nt in ["C4", "G4", "E5", "B5"]:                                             # тихий аккорд в конце
        pluck(out, 2.0, hz(nt), amp=0.45, bright=0.3)
    return finish(room(out, wet=0.3, decay=0.6))


def dawn():
    dur = 6.0
    n = int(SR * dur)
    t = np.arange(n) / SR
    pad = np.zeros(n)
    for nt in ["F3", "C4", "E4", "G4", "A4"]:
        f = hz(nt)
        for det in (-0.12, 0.0, 0.13):                                              # три голоса чуть врозь — «хор»
            ph = rng.uniform(0, 2 * math.pi)
            vib = 0.0025 * np.sin(2 * math.pi * 4.8 * t + ph)
            pad += np.sin(2 * math.pi * f * (1 + det / 100) * t * (1 + vib) + ph) + 0.18 * np.sin(4 * math.pi * f * t + ph)
    swell = np.sin(np.pi * np.clip(t / (dur - 0.3), 0, 1)) ** 1.6                  # нарастает и стихает — вдох
    pad = lowpass(pad, 2400) * swell * 0.11
    out = pad.copy()
    for st, nt, a in [(0.9, "C6", 0.5), (1.5, "E6", 0.45), (2.05, "G6", 0.5), (2.6, "A6", 0.42), (3.3, "G6", 0.35), (3.9, "E6", 0.3)]:
        strike(out, st, hz(nt), BELL, amp=a * 0.6)
    return finish(room(out, wet=0.35, decay=0.7), fade=0.4)


def fm(out, start, f, dur=1.6, amp=1.0, ratio=3.5, index=2.2, decay=0.55, glide=0.0):
    """FM-колокол (несущая + модулятор с нецелым отношением): стеклянно-металлический, узнаваемый тембр.
    glide — лёгкий «подъезд» высоты в начале ноты (доля частоты)."""
    i0 = int(SR * start)
    n = min(int(SR * dur), len(out) - i0)
    t = np.arange(n) / SR
    env = np.minimum(1, t / 0.003) * np.exp(-t / decay)
    menv = index * np.exp(-t / (decay * 0.35))                    # яркость быстро уходит — остаётся чистый тон
    ff = f * (1 - glide * np.exp(-t / 0.035))
    ph = 2 * math.pi * np.cumsum(ff) / SR
    out[i0:i0 + n] += amp * env * np.sin(ph + menv * np.sin(ph * ratio))


def signature():
    """«Фирменный»: мотив Dewiar — вверх на квинту, мягко вниз на терцию и искра октавой выше. Короткий и узнаваемый."""
    out = np.zeros(int(SR * 3.6))
    for st, nt, a, g in [(0.04, "G5", 0.7, 0.03), (0.2, "D6", 0.75, 0.02), (0.42, "B5", 0.65, 0.0)]:
        fm(out, st, hz(nt), dur=1.8, amp=a, glide=g)
    fm(out, 0.62, hz("D7"), dur=1.0, amp=0.18, ratio=2.0, index=1.2, decay=0.35)
    for st, nt, a, g in [(1.55, "G5", 0.5, 0.03), (1.71, "D6", 0.55, 0.02), (1.93, "G6", 0.5, 0.0)]:     # ответ — выше и светлее
        fm(out, st, hz(nt), dur=1.5, amp=a, glide=g)
    return finish(room(out, wet=0.26, decay=0.5))


def drops():
    """«Капли»: капли воды — короткие тоны с быстрым подъёмом высоты, складываются в мелодию."""
    out = np.zeros(int(SR * 3.4))
    def drop(st, f, a):
        i0 = int(SR * st); n = int(SR * 0.35); t = np.arange(n) / SR
        ff = f * (0.55 + 0.45 * (1 - np.exp(-t / 0.018)))          # «блоп»: высота быстро взлетает
        ph = 2 * math.pi * np.cumsum(ff) / SR
        out[i0:i0 + n] += a * np.sin(ph) * np.minimum(1, t / 0.006) ** 2 * np.exp(-t / 0.075)
    for st, nt in [(0.05, "E6"), (0.2, "G6"), (0.35, "B6"), (0.62, "A6"), (0.8, "E6"), (1.2, "D6"), (1.35, "E6"), (1.5, "G6"), (1.68, "B6"), (1.86, "E7")]:
        drop(st, hz(nt), 0.8)
    return finish(room(out, wet=0.34, decay=0.55))


def orbit():
    """«Орбита»: мягкий взлёт и пузырьки-арпеджио с эхом — футуристично, но не резко."""
    dur = 4.2
    out = np.zeros(int(SR * dur))
    n = int(SR * 0.6); t = np.arange(n) / SR
    f = 220 * (2 ** (t / 0.6 * 2.5))
    out[:n] += 0.12 * np.sin(2 * math.pi * np.cumsum(f) / SR) * np.sin(np.pi * t / 0.6) ** 2
    seq = ["C6", "G6", "D7", "G6", "E7", "D7"]
    for i, nt in enumerate(seq):
        for rep, gain in ((0, 1.0), (0.21, 0.45), (0.42, 0.2)):     # эхо
            fm(out, 0.55 + i * 0.13 + rep, hz(nt), dur=0.9, amp=0.45 * gain, ratio=2.0, index=1.6, decay=0.22)
    return finish(room(out, wet=0.38, decay=0.65))


SOUNDS = {"signature": signature, "drops": drops, "orbit": orbit, "chime": chime, "marimba": marimba, "harp": harp, "dawn": dawn}


def main():
    dst = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "android/res/raw")
    dst.mkdir(parents=True, exist_ok=True)
    for name, fn in SOUNDS.items():
        x = fn()
        pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            with wave.open(tmp.name, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
            out = dst / f"alarm_{name}.ogg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp.name, "-c:a", "libvorbis", "-q:a", "5", str(out)], check=True)
        print(f"{out}  {len(x) / SR:.1f} с  {out.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
