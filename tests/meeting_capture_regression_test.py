"""Regression checks for audio loss, blocked stop, and native helper lifetime. No devices/TCC."""
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
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

    def test_automatic_ocr_does_not_block_frame_capture(self):
        import io
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            pr = Prompter(Engine(Path(td)))
            pr.on = True
            pr.meeting = {"title": "OCR", "started": time.time(), "record": True}
            pr._transcript_open("OCR")
            pr._video_enabled = True
            entered, release = threading.Event(), threading.Event()
            def recognize(data):
                entered.set()
                release.wait(3)
                return "Принято решение 42"
            image = io.BytesIO()
            Image.new("RGB", (96, 54), "white").save(image, "PNG")
            try:
                with patch("ocr.recognize", side_effect=recognize):
                    before = time.monotonic()
                    frame = pr.capture_frame(image.getvalue(), auto=True)
                    self.assertLess(time.monotonic() - before, 0.3)
                    self.assertTrue(entered.wait(1))
                    self.assertTrue((Path(pr._transcript_path).parent / frame["path"]).is_file())
                    self.assertIsNone(pr._video_dir, "с аудио не нужны лишние покадровые JPEG")
                    pr.stop(summary=False)
                    release.set()
                    pr._ocr_thread.join(2)
                    self.assertEqual(pr._screenshots[0]["ocr"], "Принято решение 42")
            finally:
                release.set()
                deadline = time.monotonic() + 3
                while pr._post_busy and time.monotonic() < deadline:
                    time.sleep(0.01)
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
