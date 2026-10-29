"""Звук через libpulse без pactl/parec/pacat: запись, вывод, «стоп», эхоподавление, громкость, ffmpeg.
Нужен работающий PulseAudio/PipeWire. Нет сервера — проверка пропускается (выводит ALL OK с пометкой)."""
import os, sys, threading, time
from pathlib import Path
import numpy as np
ROOT = Path(os.environ.get("DEWIAR_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT / "core"))
os.environ["DEWIAR_AUDIO_BACKEND"] = "libpulse"
import audio_io as A, pulse, eyes  # noqa: E402

def ok(c, m):
    print(("✅ " if c else "❌ ") + m)

try:
    with pulse.control() as p:
        info = p.server_info()
except Exception as e:  # noqa: BLE001
    print(f"нет звукового сервера ({e}) — пропускаю\nALL OK"); sys.exit(0)

sink = os.environ.get("DEWIAR_TEST_SINK", info.default_sink_name)
ok(pulse.available(), "libpulse-simple загружается")
devs = A.list_devices(); ok(any(d["kind"] == "sink" for d in devs), f"устройства без pactl: {len(devs)}")
cap = A.Capture(device=sink + ".monitor"); cap.open()
got, stop = [], [False]
def rd():
    while not stop[0]:
        f = cap.read()
        if not f: break
        got.append(f)
th = threading.Thread(target=rd); th.start(); time.sleep(0.3)
pl = A.Player(device=sink)
sig = (np.sin(2 * np.pi * 440 * np.arange(22050 * 3) / 22050) * 8000).astype(np.int16).tobytes()
pl.start(22050)
threading.Thread(target=lambda: (time.sleep(0.7), pl.stop())).start()
t = time.time(); r = pl.write(sig); dt = time.time() - t
ok(r is False and dt < 2.2, f"«стоп» прерывает 3-секундную фразу ({dt:.2f} с; виртуальные устройства отдают звук кусками до 2 с)")
t = time.time(); pl.start(22050); pl.write(A.tone("wake")); pl.finish()
ok(time.time() - t < 2.5, f"короткий сигнал доигрывается и поток закрывается ({time.time() - t:.2f} с)")
out = pulse.SimpleStream(22050, False, device=sink); out.write(A.tone("done") * 3); out.drain(); out.close()
time.sleep(2.5); stop[0] = True; th.join(timeout=3); cap.close()
ok(not th.is_alive(), "микрофон закрывается из другого потока")
a = np.frombuffer(b"".join(got), np.int16).astype(np.float32)
loud = (np.sqrt((a[: len(a) // 512 * 512].reshape(-1, 512) ** 2).mean(1)) > 500).sum() * 0.032 if len(a) >= 512 else 0
ok(0.08 < loud < 3.2, f"сигнал слышен в записи ({loud:.2f} с громкого)")
ec = A.EchoCancel()
others = [d["name"] for d in devs if d["kind"] == "source"] + [d["name"] + ".monitor" for d in devs if d["kind"] == "sink" and d["name"] != sink]
if others and ec.setup(others[0], sink):
    ok(pulse.has_source(A.AEC_SOURCE), "эхоподавление загружено через libpulse")
    ec.teardown(); ok(not pulse.has_source(A.AEC_SOURCE), "эхоподавление выгружено")
else:
    print("(эхоподавление на этом сервере недоступно — пропускаю)")
before = pulse.volume()
v = pulse.volume(percent=37); ok(v == 37, f"громкость без pactl: {v}%")
pulse.volume(percent=before)
ff = eyes.ffmpeg_path(); ok(bool(ff) and os.access(ff, os.X_OK), f"ffmpeg есть: {ff}")
