"""Минимальный тест диктовки в поле: VAD-фраза идёт отдельным маркером и не вызывает handle_text."""
import queue
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from engine_audio import AudioMixin, RATE

events = []
q = queue.Queue()
m = SimpleNamespace(
    server=False, hold=None, dictation_active=False, dictation_finish_requested=False,
    wake_time=0.0, follow_up_until=0.0, state="idle", stop_utt=False, stt_q=q,
)
m.set_state = lambda state: setattr(m, "state", state)
m.emit = lambda kind, **data: events.append((kind, data))
m.interrupt = lambda *args, **kwargs: None

assert AudioMixin.dictation_start(m)["state"] == "recording"
assert m.dictation_active and m.state == "listening"
AudioMixin._on_utterance(m, b"\0" * (RATE * 2), 0.0, 1.0)
item = q.get_nowait()
assert item[4] == "dictation", item
assert not any(kind == "handle_text" for kind, _ in events)
assert AudioMixin.dictation_finish(m)["state"] == "finishing"
assert AudioMixin.dictation_cancel(m)["state"] == "cancelled"
print("OK: диктовка отделена от отправки вопроса")
