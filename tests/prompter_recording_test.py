"""Проверки записи онлайн-встречи без захвата микрофона Dewiar."""
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from prompter import Prompter  # noqa: E402
import audio_io  # noqa: E402


class FakeEngine:
    def __init__(self, root):
        self.events = []
        self.workspace = SimpleNamespace(ensure=lambda: root)
        self.s = SimpleNamespace(active_llm=lambda: None)
        self.audio_suspended = threading.Event()
        self.restart_audio = threading.Event()

    def emit(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def set_state(self, *_):
        pass

    def transcribe_file(self, path):
        assert Path(path).is_file()
        return [(0.0, 2.0, "Обсудили сроки"), (2.5, 5.0, "Договорились прислать договор")]


root = Path(tempfile.mkdtemp())
p = Prompter(FakeEngine(root))
assert p._meta_list("Иван, Анна\nКоманда") == "Иван, Анна, Команда"
preview = p._summary_preview({"started": 1, "participants": "Иван", "topics": "сроки"})
assert preview["participants"] == "Иван" and preview["topics"] == "сроки" and "будут определены" in preview["decisions"]
p._suspend_engine_mic(True)
assert p.eng.audio_suspended.is_set()
p._suspend_engine_mic(False)
assert not p.eng.audio_suspended.is_set() and p.eng.restart_audio.is_set()
p._record_open("Онлайн / переговоры")
p._record_write(b"\0\0" * 512)
p._record_close()
assert Path(p._record_path).is_file()
p.meeting = {"title": "Тест", "record": True, "deferred": True, "started": 1, "keep_text": True,
             "participants": "Иван", "topics": "сроки", "goal": "договор", "place": "online"}
p._summarize = lambda: p.eng.events.append((("summarized",), {}))
p._post_transcribe()
assert [x["text"] for x in p.lines] == ["Обсудили сроки", "Договорились прислать договор"]
assert any(e[0] == ("summarized",) for e in p.eng.events)

# На macOS виртуальный источник должен быть выбран, а обычный микрофон — никогда.
old_mac, old_sd = audio_io.MAC, sys.modules.get("sounddevice")
audio_io.MAC = True
sys.modules["sounddevice"] = SimpleNamespace(query_devices=lambda: [
    {"name": "MacBook Microphone", "max_input_channels": 1},
    {"name": "BlackHole 2ch", "max_input_channels": 2},
])
try:
    idx, name = audio_io._sd_loopback_index()
    assert (idx, name) == (1, "BlackHole 2ch")
finally:
    audio_io.MAC = old_mac
    if old_sd is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = old_sd

print("OK: запись системного звука, отложенная транскрибация и безопасный Mac loopback")
