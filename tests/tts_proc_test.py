"""Голоса Vosk TTS — в отдельном процессе (tts.VoskProc + core/tts_worker.py).
Было: загрузка модели (onnxruntime, словарь ударений) и синтез держали GIL, и после каждого запуска ядро больше минуты
не отвечало — студия пустая, виджет «загружаю слух и голос». Подменная библиотека vosk_tts грузится 3 с, держа GIL
(чистый цикл Python), и синтезирует так же. Проверяем:
- пока голоса грузятся, поток ядра тикает без задержек (раньше — замирал на всё время загрузки);
- синтез работает (речь — PCM int16), дикторы, скорость доходят до библиотеки;
- словарь ударений — на диске (dewiar-dic.sqlite), процесс не держит его в памяти (отчёт 11.10: 1,6 ГБ), вариант
  с наибольшей вероятностью — как у vosk_tts; второй запуск словарь не собирает заново;
- процесс голосов упал — следующая фраза поднимает его сама; ошибка синтеза — исключение, а не зависание."""
import json, os, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
FAILS = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        FAILS.append(m)


fake = tmp / "fake" / "vosk_tts"
fake.mkdir(parents=True)
(fake / "__init__.py").write_text("from .model import Model\nfrom .synth import Synth\n")
(fake / "model.py").write_text('''
import json, os, time
class Model:
    def __init__(self, model_path=None, **kw):
        end = time.time() + float(os.environ.get("FAKE_LOAD", "3"))
        x = 0
        while time.time() < end:          # как onnxruntime: держим GIL
            x += 1
        self.dic, n = {}, 0
        for line in open(model_path + "/dictionary", encoding="utf-8"):
            k, p, v = line.split(maxsplit=2); self.dic[k] = v; n += 1
        with open(model_path + "/reads.log", "a") as f:
            f.write(f"{n}\\n")
''')
(fake / "synth.py").write_text('''
import numpy as np
class Synth:
    def __init__(self, model): self.m = model
    def synth_audio(self, text, speaker_id=0, speech_rate=1.0):
        if text == "сломайся": raise ValueError("плохой текст")
        if text == "умри": import os; os._exit(3)
        n = int(2205 * (1 + speaker_id)) + len(self.m.dic)
        return (np.sin(np.arange(n) / 5) * 8000 * float(speech_rate or 1)).astype(np.int16)
''')
model = tmp / "vosk-model-tts-ru-0.9-multi"
model.mkdir()
(model / "model.onnx").write_bytes(b"x")
(model / "dictionary").write_text("".join(f"слово{i} 1.0 s l o v o\n" for i in range(5000)), encoding="utf-8")
os.environ["PYTHONPATH"] = str(tmp / "fake") + os.pathsep + os.environ.get("PYTHONPATH", "")
sys.path.insert(0, str(ROOT / "core"))
import tts  # noqa: E402

gaps, stop = [], threading.Event()


def ticker():
    last = time.monotonic()
    while not stop.is_set():
        time.sleep(0.05)
        now = time.monotonic(); gaps.append(now - last); last = now


th = threading.Thread(target=ticker, daemon=True); th.start()
v = tts.VoskTTS(tmp)
t0 = time.monotonic()
v._load(model)
load = time.monotonic() - t0
stop.set(); th.join()
ok(v.ready and load >= 2.5, f"модель загружена (в отдельном процессе за {load:.1f} с)")
ok(max(gaps) < 0.5, f"пока голоса грузились, ядро не замирало (самая долгая пауза {max(gaps):.2f} с)")
ok(len(v.speakers) == 12 and all("gender" in s for s in v.speakers), f"дикторы определены: {len(v.speakers)}")
rate, pcm = next(v.synth("Привет, мир", {"vosk": 1}, speed=1.0))
ok(rate == 22050 and len(pcm) == 2 * (2205 * 2 + 5000), f"синтез через процесс: {len(pcm)} байт")
ok((model / "dewiar-dic.sqlite").exists() and not (model / "dewiar-dic.pickle").exists(), "словарь ударений — на диске (sqlite), не в памяти")
reads1 = (model / "reads.log").read_text().split()
built = (model / "dewiar-dic.sqlite").stat().st_mtime
v.synth_.close()
v2 = tts.VoskTTS(tmp)
(model / "dewiar-speakers.json").unlink()
v2._load(model)
reads2 = (model / "reads.log").read_text().split()
rate, pcm = next(v2.synth("Ещё раз", {"vosk": 0}))
ok(reads2[-1] == "0" and reads1[-1] == "0" and len(pcm) == 2 * (2205 + 5000) and (model / "dewiar-dic.sqlite").stat().st_mtime == built,
   f"модель словарь не разбирает, второй запуск не собирает его заново (строк прочитано моделью: {reads1[-1]} → {reads2[-1]}), слова на месте")
# как vosk_tts: у слова — вариант с наибольшей вероятностью; «in» и [] работают; нет слова — KeyError
import tts_worker as TW  # noqa: E402
d2 = tmp / "d2"; d2.mkdir()
(d2 / "dictionary").write_text("дом 0.3 d o m1\nдом 0.9 d o m2\nдом 0.5 d o m3\nкот 1.0 k o t\n", encoding="utf-8")
TW.build_dic(d2 / "dictionary", d2 / "x.sqlite", [1, 2])
sd = TW.SqlDic(d2 / "x.sqlite")
try:
    sd["пёс"]; miss = False
except KeyError:
    miss = True
ok(sd["дом"].strip() == "d o m2" and "кот" in sd and "пёс" not in sd and miss and len(sd) == 2, "словарь на диске: лучший вариант, in, [], KeyError")
# память процесса голосов: словарь на 400 тыс. слов в памяти и на диске
big = tmp / "vosk-model-tts-big"; big.mkdir()
(big / "model.onnx").write_bytes(b"x")
(big / "dictionary").write_text("".join(f"словоформа{i} 1.0 s l o v o f o r m a {i}\n" for i in range(400000)), encoding="utf-8")
os.environ["FAKE_LOAD"] = "0"


def worker_rss(mode):
    os.environ["DEWIAR_TTS_DIC"] = mode
    for f in big.glob("dewiar-*"):
        f.unlink()
    w = tts.VoskTTS(tmp); w._load(big)
    next(w.synth("Привет", {"vosk": 0}))
    rss = int(re.search(r"VmRSS:\s+(\d+)", Path(f"/proc/{w.synth_.p.pid}/status").read_text()).group(1)) // 1024
    w.synth_.close()
    return rss


import re  # noqa: E402
if Path("/proc/self/status").exists():
    mem, disk = worker_rss("memory"), worker_rss("disk")
    ok(disk < mem - 40, f"процесс голосов: словарь на диске — {disk} МБ вместо {mem} МБ")
os.environ.pop("DEWIAR_TTS_DIC", None)
try:
    v2.synth_.synth_audio("сломайся"); ok(False, "ошибка синтеза — исключение")
except RuntimeError as e:
    ok("плохой текст" in str(e), f"ошибка синтеза — исключение с причиной: {e}")
pid = v2.synth_.p.pid
try:
    v2.synth_.synth_audio("умри")
except RuntimeError:
    pass
a = v2.synth_.synth_audio("снова", speaker_id=0)
ok(len(a) == 2205 + 5000 and v2.synth_.p.pid != pid, "процесс голосов упал — следующая фраза подняла его заново")
v2.synth_.close()
print("ALL OK" if not FAILS else f"ошибок: {len(FAILS)}")
sys.exit(1 if FAILS else 0)
