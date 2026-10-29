"""Голосовой отпечаток имени: узнаём имя персонажа по звучанию голоса владельца, без словаря распознавателя.

Как голосовые метки в старых телефонах: при обучении (калибровка, engine.py) человек трижды говорит имя, сохраняем
признаки звука (MFCC) образцов; в работе начало фразы сравниваем с ними выравниванием по времени (DTW), поэтому
совпадает и медленное, и быстрое произношение. Работает для любого слова («Девиар», «Нова»), быстро и без моделей.
match() сообщает и где имя кончается — остаток фразы engine_audio.py отдаёт распознавателю уже без имени.
Хранилище STATE_DIR/voiceprints/<персонаж>/ — то же, что VOICEPRINT_DIR в personas.py и папка в backup.py.
"""

import json
import logging
import threading

import numpy as np

from settings import STATE_DIR

log = logging.getLogger("dewiar.voiceprint")
RATE, WIN, HOP, NFFT, NMEL, NCEP = 16000, 400, 160, 512, 26, 13
DIR = STATE_DIR / "voiceprints"
SEARCH_START = 1.2          # имя должно начаться в первые 1.2 с фразы (можно после «эй»)
SEARCH_WINDOW = 3.0         # дальше 3 с фразы не смотрим


def _mel_bank():
    def hz2mel(h):
        return 2595 * np.log10(1 + h / 700)

    def mel2hz(m):
        return 700 * (10 ** (m / 2595) - 1)
    pts = mel2hz(np.linspace(hz2mel(80), hz2mel(7600), NMEL + 2))
    bins = np.floor((NFFT + 1) * pts / RATE).astype(int)
    fb = np.zeros((NMEL, NFFT // 2 + 1), dtype=np.float32)
    for m in range(1, NMEL + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        for k in range(l, c):
            fb[m - 1, k] = (k - l) / max(1, c - l)
        for k in range(c, r):
            fb[m - 1, k] = (r - k) / max(1, r - c)
    return fb


_FB = _mel_bank()
_DCT = np.cos(np.pi / NMEL * (np.arange(NMEL)[None, :] + 0.5) * np.arange(NCEP)[:, None]).astype(np.float32)
_WINF = np.hamming(WIN).astype(np.float32)


def features(pcm: bytes):
    """-> (кадры × 24) нормированные MFCC c1..c12 + их изменения; и громкость кадров."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if len(x) < WIN:
        return np.zeros((0, 24), np.float32), np.zeros(0, np.float32)
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    n = 1 + (len(x) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * _WINF
    power = np.abs(np.fft.rfft(frames, NFFT)) ** 2 / NFFT
    energy = 10 * np.log10(power.sum(1) + 1e-10)
    mel = np.log(power @ _FB.T + 1e-8)
    cep = (mel @ _DCT.T)[:, 1:NCEP]
    delta = np.zeros_like(cep)
    if len(cep) > 4:
        delta[2:-2] = (cep[3:-1] - cep[1:-3] + 2 * (cep[4:] - cep[:-4])) / 10
    f = np.hstack([cep, delta])
    f = (f - f.mean(0)) / (f.std(0) + 1e-5)                   # нормировка под микрофон и громкость
    return f.astype(np.float32), energy.astype(np.float32)


def trim(f, energy):
    """Отрезать тишину по краям образца."""
    if len(energy) == 0:
        return f
    thr = max(energy.max() - 35, np.percentile(energy, 20) + 6)
    on = np.where(energy > thr)[0]
    if len(on) == 0:
        return f
    return f[max(0, on[0] - 2): on[-1] + 3]


def sdtw(t, u, start_max):
    """Поиск образца t (n×d) в начале фразы u (m×d): начало — в первых start_max кадрах, конец — где угодно.
    Шаги с наклоном 1/2…2, без зависимостей внутри строки — строка считается целиком numpy.
    -> (средняя цена пути на кадр образца, кадр конца совпадения)."""
    n, m = len(t), len(u)
    if n < 5 or m < n // 2:
        return float("inf"), 0
    tn = t / (np.linalg.norm(t, axis=1, keepdims=True) + 1e-6)
    un = u / (np.linalg.norm(u, axis=1, keepdims=True) + 1e-6)
    c = 1.0 - tn @ un.T                                       # косинусное расстояние, 0…2
    inf = np.float32(1e9)
    D = np.full((n, m), inf, dtype=np.float32)
    D[0, :start_max] = c[0, :start_max]
    for i in range(1, n):
        best = np.full(m, inf, dtype=np.float32)
        best[1:] = D[i - 1, :-1]                                   # по диагонали
        best[2:] = np.minimum(best[2:], D[i - 1, :-2] + c[i, 1:-1])  # фраза быстрее образца
        if i >= 2:
            best[1:] = np.minimum(best[1:], D[i - 2, :-1] + c[i - 1, 1:])  # фраза медленнее
        D[i] = c[i] + best
    j = int(np.argmin(D[n - 1]))
    return float(D[n - 1, j]) / n, j


class Voiceprints:
    """Образцы имён: STATE_DIR/voiceprints/<персонаж>/{0,1,2}.npy + meta.json {порог}."""

    def __init__(self):
        self._lock = threading.Lock()
        self.items = {}
        self.sensitivity = 1.0
        self.reload()

    def reload(self):
        items = {}
        if DIR.is_dir():
            for d in sorted(p for p in DIR.iterdir() if p.is_dir()):
                try:
                    meta = json.loads((d / "meta.json").read_text())
                    tpl = [np.load(f) for f in sorted(d.glob("*.npy"))]
                    if tpl:
                        items[d.name] = {"templates": tpl, "threshold": float(meta["threshold"])}
                except (OSError, ValueError, KeyError) as e:
                    log.warning("Отпечаток %s не прочитан: %s", d.name, e)
        with self._lock:
            self.items = items

    def has(self, pid):
        return pid in self.items

    def train(self, pid, samples):
        """samples — PCM образцов имени (16 кГц, int16). Порог — по тому, насколько образцы похожи друг на друга.
        Перезаписывает папку персонажа. -> {"samples", "threshold", "cross"} для окна калибровки;
        ValueError, если годных (не коротких и не тихих) образцов меньше двух."""
        tpl = []
        for pcm in samples:
            f, e = features(pcm)
            t = trim(f, e)
            if len(t) >= 12:
                tpl.append(t)
        if len(tpl) < 2:
            raise ValueError("образцы слишком короткие или тихие — скажите имя погромче")
        cross = []
        for a in range(len(tpl)):
            for b in range(len(tpl)):
                if a != b:
                    cross.append(sdtw(tpl[a], tpl[b], max(3, len(tpl[b]) // 3))[0])
        # С запасом к худшему сходству образцов между собой; рамки 0.28…0.42 — чтобы небрежные образцы
        # не дали ложных срабатываний на любую речь, а слишком одинаковые — не требовали идеального повтора.
        threshold = float(min(0.42, max(0.28, max(cross) * 1.6 + 0.10)))
        d = DIR / pid
        if d.exists():
            for f in d.iterdir():
                f.unlink()
        d.mkdir(parents=True, exist_ok=True)
        for i, t in enumerate(tpl):
            np.save(d / f"{i}.npy", t)
        (d / "meta.json").write_text(json.dumps({"threshold": threshold, "cross": cross}))
        self.reload()
        log.info("Отпечаток «%s»: %d образцов, похожесть между ними %s, порог %.3f",
                 pid, len(tpl), [round(x, 3) for x in cross], threshold)
        return {"samples": len(tpl), "threshold": round(threshold, 3), "cross": [round(x, 3) for x in cross]}

    def forget(self, pid):
        d = DIR / pid
        if d.exists():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        self.reload()

    def match(self, pcm, allowed=None):
        """pcm — фраза (16 кГц, int16); allowed — какие персонажи могут откликаться (None — все).
        -> (персонаж, конец имени в секундах от начала pcm, расстояние) или (None, 0, лучшее расстояние).
        Порог персонажа умножается на sensitivity (настройка audio.name_sensitivity): больше — срабатывает охотнее."""
        with self._lock:
            items = dict(self.items)
        if not items:
            return None, 0.0, float("inf")
        f, _ = features(pcm[: int(SEARCH_WINDOW * RATE) * 2])
        start_max = int(SEARCH_START * RATE / HOP)
        best = (None, 0.0, float("inf"), 0.0)
        for pid, it in items.items():
            if allowed is not None and pid not in allowed:
                continue
            # При 3+ образцах — среднее двух лучших: случайное сходство с одним образцом не срабатывает.
            ds = sorted(sdtw(t, f, start_max) for t in it["templates"])
            d, j = ds[0] if len(ds) < 3 else (sum(x[0] for x in ds[:2]) / 2, ds[0][1])
            margin = d / (it["threshold"] * self.sensitivity)
            if margin < 1.0 and (best[0] is None or margin < best[3]):
                best = (pid, (j + 1) * HOP / RATE + WIN / RATE, d, margin)
            elif best[0] is None and d < best[2]:
                best = (None, 0.0, d, margin)
        return best[0], best[1], best[2]
