"""Обработка голоса ответа (пресеты «Теплее», «Кино», «ИИ из фильма»…) — только numpy, без внешних библиотек.

  • высота — без обработки звука: engine_remote.py просит синтезатор говорить в 1/pitch раз быстрее и играет звук
    с частотой rate·pitch; темп прежний, голос ниже и «крупнее» (сдвигаются и форманты). Здесь — только pitch_of();
  • тембр и пространство — одна свёртка с самодельной импульсной характеристикой: линейно-фазовый эквалайзер,
    ранние отражения, затухающий «хвост» зала; для «ИИ» — ещё гребенчатый фильтр («металл») и хорус.
Свёртка потоковая (overlap-add через FFT): куски от синтезатора обрабатываются по мере прихода, хвост реверберации
переходит в следующий кусок и доигрывается flush() в конце реплики. Список пресетов уходит в окно через catalog().
"""

import numpy as np

# id: (подпись, описание, параметры). pitch — множитель высоты; low/presence/air — усиление полос эквалайзера, дБ;
# room — время затухания зала RT60, с; wet — доля зала; predelay — задержка до зала, с; metal, chorus — сила эффектов.
PRESETS = {
    "off":     ("Без обработки", "как есть", {}),
    "clear":   ("Чище", "теплее и разборчивее, без эха",
                {"pitch": 1.0, "low": 3.0, "presence": 2.0, "air": -1.0}),
    "warm":    ("Теплее", "чуть ниже, мягкий объём",
                {"pitch": 0.97, "low": 4.0, "presence": 1.5, "air": -2.0, "room": 0.35, "wet": 0.07}),
    "deep":    ("Глубже", "ниже и плотнее, небольшая комната",
                {"pitch": 0.92, "low": 5.0, "presence": 1.5, "air": -2.5, "room": 0.45, "wet": 0.09}),
    "cinema":  ("Кино", "низкий голос в большом зале",
                {"pitch": 0.93, "low": 5.0, "presence": 2.0, "air": -2.0, "room": 1.7, "wet": 0.22, "predelay": 0.035}),
    "ai":      ("ИИ из фильма", "ниже, металлический отблеск и пространство",
                {"pitch": 0.94, "low": 4.0, "presence": 3.0, "air": 0.0, "room": 0.9, "wet": 0.16, "predelay": 0.02,
                 "metal": 0.28, "chorus": 0.22}),
    "space":   ("Космос", "далёкий голос в огромном пространстве",
                {"pitch": 0.95, "low": 3.0, "presence": 1.0, "air": -3.0, "room": 3.2, "wet": 0.32, "predelay": 0.06,
                 "chorus": 0.15}),
}


def catalog():
    return [{"id": k, "name": v[0], "description": v[1]} for k, v in PRESETS.items()]


def params(name):
    return dict(PRESETS.get(name or "off", PRESETS["off"])[2])


def pitch_of(name):
    return float(params(name).get("pitch", 1.0))


def _eq_fir(rate, low_db, presence_db, air_db, taps=257):
    """Линейно-фазовый КИХ-эквалайзер: полка снизу (<180 Гц), подъём присутствия (2.5–5 кГц), «воздух» (>8 кГц)."""
    n = 4096
    f = np.fft.rfftfreq(n, 1 / rate)
    g_db = (low_db / (1 + (f / 180.0) ** 2)
            + presence_db * np.exp(-0.5 * ((np.log2(np.maximum(f, 1) / 3500.0)) / 0.6) ** 2)
            + air_db / (1 + (8000.0 / np.maximum(f, 1)) ** 4))
    g_db -= 0.35 * low_db                              # не громче в целом: басы добавили — чуть убавили общий уровень
    h = np.fft.irfft(10 ** (g_db / 20), n)
    h = np.roll(h, taps // 2)[:taps] * np.hanning(taps)
    return h.astype(np.float32)


def _reverb_ir(rate, rt60, predelay, seed=5):
    """Искусственный зал: несколько ранних отражений + шум с экспоненциальным затуханием,
    высокие частоты гаснут быстрее низких (как в настоящем помещении)."""
    rnd = np.random.default_rng(seed)
    length = int(rate * min(4.5, rt60 * 1.2)) + 1
    t = np.arange(length) / rate
    noise = rnd.standard_normal(length)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(length, 1 / rate)
    lo = np.fft.irfft(spec * (freqs < 1500), length)
    hi = np.fft.irfft(spec * (freqs >= 1500), length)
    tail = lo * np.exp(-6.9 * t / rt60) + 0.55 * hi * np.exp(-6.9 * t / (rt60 * 0.45))
    tail *= np.minimum(1.0, t / 0.03)                    # мягкое начало хвоста
    ir = np.zeros(length + int(predelay * rate) + 1)
    pd = int(predelay * rate)
    ir[pd:pd + length] += tail / (np.sqrt(np.sum(tail ** 2)) + 1e-9)
    for d, g in ((0.011, 0.5), (0.019, -0.38), (0.027, 0.3), (0.041, -0.22)):   # ранние отражения
        k = pd + int(d * rate)
        if k < len(ir):
            ir[k] += g * 0.35
    return ir.astype(np.float32)


class VoiceFX:
    """Обработка одной реплики на частоте синтезатора rate (int16 моно). process() — по кускам, flush() — доиграть
    хвост. Состояние (хвост свёртки, история хоруса) — на одну реплику: при смене частоты нужен новый объект."""

    def __init__(self, name, rate):
        self.p = params(name)
        self.rate = rate
        self.on = bool(self.p) and name != "off"
        self.tail = np.zeros(0, np.float32)
        self.n = 0                                       # номер сэмпла — для хоруса
        self.hist = np.zeros(0, np.float32)              # прошлые сэмплы — для хоруса
        if not self.on:
            return
        p = self.p
        h = _eq_fir(rate, p.get("low", 0.0), p.get("presence", 0.0), p.get("air", 0.0))
        dry = h
        if p.get("metal"):                               # гребёнка ~180 и ~260 Гц: «металл» ИИ из кино
            comb = np.zeros(int(0.012 * rate) + 1, np.float32)
            comb[0] = 1.0
            comb[int(0.0055 * rate)] += p["metal"]
            comb[int(0.0038 * rate)] += p["metal"] * 0.6
            comb[int(0.0115 * rate)] += p["metal"] * 0.35
            dry = np.convolve(dry, comb).astype(np.float32)
        ir = dry
        if p.get("room"):
            rv = np.convolve(_reverb_ir(rate, p["room"], p.get("predelay", 0.012)), h).astype(np.float32)
            wet = float(p.get("wet", 0.1))
            ir = np.zeros(max(len(dry), len(rv)), np.float32)
            ir[:len(dry)] += dry * (1 - wet * 0.5)
            ir[:len(rv)] += rv * wet * 2.2
        self.ir = ir
        self.lat = len(h) // 2                           # задержка линейно-фазового фильтра — срезаем
        self.skip = self.lat
        self.chorus = float(p.get("chorus", 0.0))

    def _chorus(self, x):
        """Лёгкий хорус: к голосу подмешивается копия с плавающей задержкой 12–20 мс."""
        if not self.chorus:
            return x
        rate = self.rate
        buf = np.concatenate([self.hist, x])
        idx = np.arange(len(x)) + len(self.hist)
        t = (self.n + np.arange(len(x))) / rate
        d = (0.016 + 0.004 * np.sin(2 * np.pi * 0.55 * t)) * rate
        pos = idx - d
        i0 = np.floor(pos).astype(np.int64)
        fr = (pos - i0).astype(np.float32)
        ok = i0 >= 0
        i0c = np.clip(i0, 0, len(buf) - 2)
        delayed = np.where(ok, buf[i0c] * (1 - fr) + buf[i0c + 1] * fr, 0.0).astype(np.float32)
        keep = int(0.03 * rate)
        self.hist = buf[-keep:]
        self.n += len(x)
        return (x + self.chorus * delayed) / (1 + self.chorus * 0.5)

    def process(self, pcm: bytes) -> bytes:
        if not self.on or not pcm:
            return pcm
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        x = self._chorus(x)
        m = len(self.ir)
        L = 1 << int(np.ceil(np.log2(len(x) + m - 1)))
        y = np.fft.irfft(np.fft.rfft(x, L) * np.fft.rfft(self.ir, L), L)[:len(x) + m - 1].astype(np.float32)
        y[:len(self.tail)] += self.tail[:len(y)]
        if len(self.tail) > len(y):                    # кусок короче хвоста — остаток хвоста переносим
            rest = self.tail[len(y):]
            y_tail = y[len(x):]
            merged = np.zeros(max(len(rest), len(y_tail)), np.float32)
            merged[:len(rest)] += rest
            merged[:len(y_tail)] += y_tail
            self.tail = merged
        else:
            self.tail = y[len(x):].copy()
        out = y[:len(x)]
        if self.skip:
            cut = min(self.skip, len(out))
            out = out[cut:]
            self.skip -= cut
        return self._to_pcm(out)

    def flush(self) -> bytes:
        """Хвост реверберации в конце реплики (с затуханием, чтобы не щёлкнуло)."""
        if not self.on or not len(self.tail):
            return b""
        t = self.tail
        self.tail = np.zeros(0, np.float32)
        # хвост тише -60 дБ не нужен
        loud = np.nonzero(np.abs(t) > 1e-3)[0]
        if not len(loud):
            return b""
        t = t[:loud[-1] + 1]
        fade = min(len(t), int(0.05 * self.rate))
        if fade:
            t[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        return self._to_pcm(t)

    @staticmethod
    def _to_pcm(y):
        a = np.abs(y)                                   # мягкий ограничитель выше 0.8 — вместо щелчков при перегрузке
        y = np.where(a < 0.8, y, np.sign(y) * (0.8 + 0.2 * np.tanh((a - 0.8) / 0.2)))
        return (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()
