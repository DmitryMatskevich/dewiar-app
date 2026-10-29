"""Первая буква ответа не пропадает (отзыв 17.10: «при озвучивании первая буква пропадает — „Я Лира“ звучит „лира“»).
Устройство вывода и эхоподавление «просыпаются» на первых долях секунды — перед ответом Player играет 0,25 с тишины;
в тестах (null) и между кусками одного ответа — без неё."""
import os, sys, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import audio_io as A  # noqa: E402


class Out:
    def __init__(self): self.data = b""
    def write(self, b): self.data += b


p = A.Player(null=True)
p.start(24000)
ok(p._lead == 0, "без звука (тесты) — без паузы")
p.null = False; p.proc = Out()
p.rate, p._stopped, p.gain, p._t0, p._written = 24000, False, 1.0, time.monotonic(), 0
p._lead = int(24000 * A.Player.LEAD) * 2
voice = b"\x10\x05" * 2400
p.write(voice); p.write(voice)
lead = len(p.proc.data) - len(p.proc.data.lstrip(b"\x00"))
ok(abs(lead - int(24000 * 0.25) * 2) <= 2 and p.proc.data.endswith(voice + voice),
   f"перед ответом — 0,25 с тишины, потом голос целиком; второй кусок — без паузы: {lead} байт тишины")
src = (ROOT / "core/audio_io.py").read_text(encoding="utf-8")
ok("self._lead = 0 if self.null else int(rate * self.LEAD) * 2" in src, "пауза ставится при каждом начале ответа (start)")
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
