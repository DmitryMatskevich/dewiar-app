"""Regression checks for audio loss, blocked stop, and native helper lifetime. No devices/TCC."""
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import audio_io
from prompter import Prompter, _Segmenter


class Settings(dict):
    def active_llm(self):
        return None


class Engine:
    def __init__(self, root):
        self.s = Settings(prompter={})
        self.workspace = SimpleNamespace(ensure=lambda: root)
        self.stopping = threading.Event()
        self.events = []

    def emit(self, *args, **kwargs):
        self.events.append((args, kwargs))


class Regression(unittest.TestCase):
    def test_final_phrase_without_silence(self):
        segment = _Segmenter(19)
        for _ in range(35):
            self.assertIsNone(segment.feed(b"\x10\x00" * 512, 0.95))
        self.assertGreater(len(segment.flush()), 16000)
        self.assertIsNone(segment.flush())

    def test_stop_does_not_wait_for_capture_close(self):
        with tempfile.TemporaryDirectory() as td:
            pr = Prompter(Engine(Path(td)))
            entered, release = threading.Event(), threading.Event()
            def close():
                entered.set()
                release.wait(3)
            pr.on = True
            pr.meeting = {"title": "fixture"}
            pr._loop_cap = SimpleNamespace(close=close)
            pr._summarize = lambda: None
            try:
                before = time.monotonic()
                result = pr.stop()
                self.assertLess(time.monotonic() - before, 0.3)
                self.assertFalse(result["on"])
                self.assertTrue(entered.wait(1))
                self.assertTrue(pr._post_busy)
            finally:
                release.set()
                deadline = time.monotonic() + 3
                while pr._post_busy and time.monotonic() < deadline:
                    time.sleep(0.01)
                pr.eng.stopping.set()
            self.assertFalse(pr._post_busy)

    def test_stt_waits_for_capture_tail(self):
        with tempfile.TemporaryDirectory() as td:
            pr = Prompter(Engine(Path(td)))
            pr._session = 7
            pr.meeting = {"started": time.time()}
            pr._capture_done.clear()
            pr._meeting_stt = lambda: SimpleNamespace(transcribe=lambda pcm: "последнее решение")
            pr._stop.set()
            q = queue.Queue()
            worker = threading.Thread(target=pr._stt_loop, args=(pr._stop, q, 7))
            worker.start()
            time.sleep(0.05)
            q.put(("them", b"\1\0" * 16000, 7, 30.0))
            pr._capture_done.set()
            worker.join(2)
            pr.eng.stopping.set()
            self.assertFalse(worker.is_alive())
            self.assertEqual(pr.lines[0]["text"], "последнее решение")

    def test_blackhole_right_channel_is_not_lost(self):
        streams = []
        class Stream:
            def __init__(self, **kwargs):
                self.options = kwargs
                streams.append(self)
            def start(self): pass
            def stop(self): pass
            def close(self): pass
        sd = SimpleNamespace(RawInputStream=Stream, PortAudioError=RuntimeError)
        with patch.dict(sys.modules, sounddevice=sd), patch.object(audio_io, "_backend", return_value="sounddevice"), patch.object(audio_io, "_sd_index", return_value=0):
            cap = audio_io.Capture(channels=2)
            cap.open()
            try:
                stereo = np.zeros((512, 2), dtype=np.int16)
                stereo[:, 1] = 6000
                streams[0].options["callback"](stereo.tobytes(), 512, None, True)
                mono = np.frombuffer(cap.read(), dtype=np.int16)
                self.assertTrue(np.all(mono == 3000))
                self.assertEqual(cap.overflows, 1)
            finally:
                cap.close()

    def test_schedule_format_accepts_legacy_and_new(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            pr = Prompter(Engine(Path(td)))
            pr._schedules_path = Path(td) / "schedules.json"
            pr._schedules_path.write_text(json.dumps([{"id": "legacy", "state": "scheduled"}]))
            pr._schedules = pr._load_schedules()
            self.assertEqual(pr._schedules[0]["id"], "legacy")
            pr._save_schedules()
            self.assertEqual(json.loads(pr._schedules_path.read_text())["version"], 1)
            self.assertEqual(pr._load_schedules()[0]["id"], "legacy")
            pr.eng.stopping.set()

    def test_automatic_ocr_is_disabled_during_recording(self):
        import io
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            pr = Prompter(Engine(Path(td)))
            pr.on = True
            pr.meeting = {"title": "OCR", "started": time.time(), "record": True}
            pr._transcript_open("OCR")
            pr._video_enabled = True
            called = threading.Event()
            def recognize(data):
                called.set()
                raise AssertionError("live OCR must remain disabled during recording")
            image = io.BytesIO()
            Image.new("RGB", (96, 54), "white").save(image, "PNG")
            try:
                with patch("ocr.recognize", side_effect=recognize):
                    before = time.monotonic()
                    frame = pr.capture_frame(image.getvalue(), auto=True)
                    self.assertLess(time.monotonic() - before, 0.3)
                    self.assertFalse(called.is_set())
                    self.assertTrue((Path(pr._transcript_path).parent / frame["path"]).is_file())
                    self.assertIsNone(pr._video_dir, "с аудио не нужны лишние покадровые JPEG")
                    pr.stop(summary=False)
            finally:
                deadline = time.monotonic() + 3
                while pr._post_busy and time.monotonic() < deadline:
                    time.sleep(0.01)
                pr.eng.stopping.set()

    def test_five_capture_errors_disable_only_frames_and_are_logged(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            pr = Prompter(Engine(Path(td)))
            pr.on = True
            pr.meeting = {"title": "fixture", "record": True, "started": time.time()}
            pr._frames_enabled = True
            pr._frame_interval = 0.001
            pr._capture_log_path = Path(td) / "session.log"
            pr._t0 = time.monotonic()
            pr.capture_frame = lambda **kwargs: (_ for _ in ()).throw(OSError("fixture capture failure"))
            pr._frame_loop(threading.Event())
            events = [json.loads(line) for line in pr._capture_log_path.read_text().splitlines()]
            self.assertFalse(pr._frames_enabled)
            self.assertTrue(pr.on, "frame capture failure must not stop meeting/audio")
            self.assertEqual([event["event"] for event in events], ["capture_error", "capture_error", "frames_disabled"])
            self.assertEqual(events[-1]["reason"], "5 consecutive capture errors")
            self.assertIn("звук продолжает записываться", pr.status)
            pr.eng.stopping.set()

    def test_memory_guard_reduces_capture_then_stops_frames(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            pr = Prompter(Engine(Path(td)))
            pr.on = True
            pr.meeting = {"title": "fixture", "record": True, "started": time.time()}
            pr._frames_enabled = True
            pr._capture_log_path = Path(td) / "session.log"
            pr._t0 = time.monotonic()
            pr._capture_resource_guard(3200, 2048)
            self.assertEqual(pr._frame_max_dimension, 1920)
            self.assertEqual(pr._frame_quality, 75)
            self.assertEqual(pr._frame_interval, 1.0)
            self.assertTrue(pr._frames_enabled)
            pr._capture_resource_guard(4300, 400)
            self.assertFalse(pr._frames_enabled)
            self.assertIn("аудиозапись продолжается", pr.status)
            events = [json.loads(line)["event"] for line in pr._capture_log_path.read_text().splitlines()]
            self.assertEqual(events, ["capture_degraded", "frames_disabled"])
            pr.eng.stopping.set()

    def test_interrupted_recording_manifest_lists_and_starts_recovery(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meeting_root = root / "Встречи"
            session = meeting_root / "Расшифровки" / "fixture"
            audio = meeting_root / "Аудио" / "fixture.wav"
            session.mkdir(parents=True)
            audio.parent.mkdir(parents=True)
            with wave.open(str(audio), "wb") as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(b"\x01\0" * 16000)
            pr = Prompter(Engine(root))
            pr.meeting = {"id": "recovery-id", "title": "fixture", "started": time.time(), "record": True,
                          "deferred": True, "audio_source": "microphone", "speaker_mode": "none"}
            pr._record_path = str(audio)
            pr._track_paths = {}
            pr._transcript_path = str(meeting_root / "Расшифровки" / "fixture.txt")
            pr._session_dir = session
            pr._capture_log_path = str(session / "session.log")
            pr._write_active_manifest()
            found = pr.interrupted_meetings()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["id"], "recovery-id")
            self.assertEqual(found[0]["duration_s"], 1)
            with patch.object(Prompter, "_transcribe_segments", lambda self, path: [(0.0, 1.0, "тестовая реплика", None)]), \
                 patch.object(Prompter, "_summary_text", lambda self, meeting, lines: "## Договорились\n- тест"):
                pr.recover_interrupted("recovery-id")
                pr._post_thread.join(5)
            self.assertFalse(pr._post_thread.is_alive(), "recovery post-processing should finish")
            self.assertEqual(pr._completed_job.meeting["title"], "fixture")
            self.assertIn("тестовая реплика", Path(pr._transcript_path).read_text())
            self.assertTrue(Path(pr._summary_path).is_file())
            self.assertFalse((session / "session.active.json").exists())
            pr.eng.stopping.set()

    @unittest.skipIf(os.name == "nt", "POSIX helper")
    def test_unresponsive_helper_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "helper"
            fake.write_text("#!" + sys.executable + '\nimport sys,time\nsys.stderr.write(\'{"ev":"ready"}\\n\');sys.stderr.flush()\ntime.sleep(30)\n')
            fake.chmod(0o700)
            cap = audio_io.TapCapture(exe=str(fake))
            cap.open()
            proc = cap.proc
            try:
                before = time.monotonic()
                self.assertEqual(cap.read(), b"")
                self.assertLess(time.monotonic() - before, 3)
            finally:
                cap.close()
            self.assertIsNotNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
