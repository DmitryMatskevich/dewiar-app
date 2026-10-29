"""Генерирует тестовые аудиосценарии (espeak-ng + ffmpeg) в папку $DEWIAR_TEST_AUDIO (по умолчанию /tmp/dewiar-test-audio)."""
import os, subprocess, wave
from pathlib import Path
D = Path(os.environ.get("DEWIAR_TEST_AUDIO", "/tmp/dewiar-test-audio")); D.mkdir(parents=True, exist_ok=True)
PH = {"a": ("Патрик, сколько будет два плюс два", 150), "b": ("Патрик, расскажи сказку", 150), "c": ("Лира, привет", 150),
      "d": ("как дела", 150), "n0": ("Игра была отличной", 160), "a2": ("Девиар, сколько будет два плюс два", 170),
      "g": ("Девиар, привет", 160), "lb": ("Девиар, подожди, расскажи лучше про космос и звёзды", 140), "yes": ("Да, выполняй", 150),
      "st": ("Хватит", 140), "nq": ("Подожди, лучше расскажи про космос", 150)}
pcm = {}
for k, (text, sp) in PH.items():
    subprocess.run(["espeak-ng", "-v", "ru", "-s", str(sp), "-w", str(D / "raw.wav"), text], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(D / "raw.wav"), "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(D / f"{k}.wav")], check=True)
    w = wave.open(str(D / f"{k}.wav")); pcm[k] = w.readframes(w.getnframes())
sil = lambda s: b"\0\0" * int(16000 * s)
def mk(name, seq):
    w = wave.open(str(D / name), "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"".join(sil(x) if isinstance(x, (int, float)) else pcm[x] for x in seq)); w.close()
mk("scenario.wav", [1, "a", 6, "b", 5, "c", 6, "d", 9])
mk("scenario2.wav", [1, "n0", 3, "a2", 6, "b", 5, "c", 6, "d", 8, "b", 5, "g", 8])
mk("scenario3.wav", [1, "b", 5, "lb", 10])
mk("tools.wav", [11, "yes", 70])
mk("scenario4.wav", [1, "b", 7, "st", 6, "b", 8, "nq", 14])
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(D / "d.wav"), "-c:a", "libopus", "-f", "ogg", str(D / "d.ogg")])  # голосовое Telegram
print(D)
