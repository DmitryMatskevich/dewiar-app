"""Запись песен на подставном источнике PCM; аудиоустройства не открываются."""
import importlib
import json
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs.store import SongStore


class FakeCapture:
    kind = 'tap'
    rate, channels = 48000, 2
    def __init__(self, **kw):
        self._cap = self
        self.sent = threading.Event()
        self.closed = threading.Event()
    def open(self): return 'Синтетический источник'
    def close(self): self.closed.set()
    def read_native(self):
        if not self.sent.is_set():
            self.sent.set()
            return b'\x00\x10' * self.rate * self.channels
        self.closed.wait(.01)
        return b''


class FakeMicCapture(FakeCapture):
    kind = ''
    rate, channels = 16000, 1
    def read(self):
        return self.read_native().replace(b'\x00\x00', b'\x00\x10')


class CaptureTest(unittest.TestCase):
    def test_stereo_duration_public_state_and_previous_capture_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(sys.modules, {'audio_io': types.SimpleNamespace(LoopbackCapture=FakeCapture, Capture=FakeMicCapture)}):
                module = importlib.import_module('songs.capture')
            store = SongStore(Path(tmp))
            sid = store.create('ТЕСТ стерео')['song_id']
            manager = module.SongCaptureManager(Path(tmp), store, lambda *a, **kw: None)
            state = manager.start(sid)
            json.dumps(state)
            handle = manager._active[sid]
            self.assertTrue(handle['capture'].sent.wait(2))
            while not handle['frames']: time.sleep(.005)
            saved = manager.stop(sid)
            self.assertEqual(saved['audio']['duration_ms'], 1000)
            self.assertEqual(saved['status'], 'queued')
            old = Path(tmp) / saved['audio']['path']
            with wave.open(str(old)) as wav:
                self.assertEqual(wav.getnchannels(), 2)
                self.assertEqual(wav.getframerate(), 48000)
                self.assertEqual(wav.getnframes(), 48000)
            manager.start(sid)
            self.assertTrue(manager._active[sid]['capture'].sent.wait(2))
            while not manager._active[sid]['frames']: time.sleep(.005)
            second = manager.stop(sid)
            self.assertNotEqual(second['audio']['path'], saved['audio']['path'])
            self.assertTrue(old.exists())
            self.assertFalse(manager.state(sid)['running'])

    def test_microphone_mode_saves_mono_recording_for_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(sys.modules, {'audio_io': types.SimpleNamespace(LoopbackCapture=FakeCapture, Capture=FakeMicCapture)}):
                module = importlib.import_module('songs.capture')
            store = SongStore(Path(tmp))
            sid = store.create('ТЕСТ микрофон')['song_id']
            manager = module.SongCaptureManager(Path(tmp), store, lambda *a, **kw: None)
            state = manager.start(sid, source='microphone')
            self.assertEqual(state['source'], 'Синтетический источник')
            self.assertTrue(manager._active[sid]['capture'].sent.wait(2))
            while not manager._active[sid]['frames']: time.sleep(.005)
            saved = manager.stop(sid)
            self.assertEqual(saved['status'], 'queued')
            with wave.open(str(Path(tmp) / saved['audio']['path'])) as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.getnframes(), 16000)


if __name__ == '__main__': unittest.main()
