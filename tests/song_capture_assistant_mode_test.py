"""Microphone song capture temporarily blocks assistant voice input and restores it."""
import sys
import types
import unittest
from pathlib import Path
from types import MethodType

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from engine_commands import CommandsMixin


class FakeCapture:
    def start(self, song_id, target, backend, source):
        return {"song_id": song_id, "running": True, "source": source}

    def stop(self, song_id):
        return {"song_id": song_id, "status": "queued"}


class FakeAnalyzer:
    def __init__(self):
        self.calls = []

    def running(self, song_id):
        return False

    def start(self, song_id, full=False):
        self.calls.append((song_id, full))
        return {"song_id": song_id, "status": "running"}


class FakeSources:
    def running(self, song_id):
        return False


class AssistantCaptureModeTest(unittest.TestCase):
    def engine(self, muted=False):
        engine = types.SimpleNamespace(
            server=False, muted=muted, song_mic_capture_active=False,
            _song_capture_resume_muted=muted, dictation_active=False, hold=None,
            song_capture=FakeCapture(), song_analyzer=FakeAnalyzer(), song_sources=FakeSources(),
            events=[], cancelled=0, states=[],
        )
        engine.emit = lambda name, **data: engine.events.append((name, data))
        engine.cancel_turn = lambda: setattr(engine, "cancelled", engine.cancelled + 1)
        engine.set_state = lambda state: engine.states.append(state)
        engine.set_mute = MethodType(CommandsMixin.set_mute, engine)
        return engine

    def test_mic_capture_mutes_assistant_then_restores_and_starts_full_analysis(self):
        engine = self.engine()
        started = CommandsMixin._command(engine, "song_capture", {"song_id": "s1", "action": "start", "source": "microphone"})
        self.assertTrue(started["running"])
        self.assertTrue(engine.muted)
        self.assertTrue(engine.song_mic_capture_active)
        with self.assertRaisesRegex(ValueError, "голосовой ввод ассистента выключен"):
            engine.set_mute(False)
        stopped = CommandsMixin._command(engine, "song_capture", {"song_id": "s1", "action": "stop"})
        self.assertEqual(stopped["status"], "running")
        self.assertFalse(engine.muted)
        self.assertFalse(engine.song_mic_capture_active)
        self.assertEqual(engine.song_analyzer.calls, [("s1", True)])

    def test_previously_muted_assistant_stays_muted(self):
        engine = self.engine(muted=True)
        CommandsMixin._command(engine, "song_capture", {"song_id": "s2", "action": "start", "source": "microphone"})
        CommandsMixin._command(engine, "song_capture", {"song_id": "s2", "action": "stop"})
        self.assertTrue(engine.muted)


if __name__ == "__main__":
    unittest.main()
