"""Capture lifecycle with synthetic PCM only: never open a physical microphone."""
import importlib
import struct
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
with patch.dict(sys.modules, {'audio_io': types.SimpleNamespace(Capture=object, LoopbackCapture=object)}):
    capture = importlib.import_module('songs.capture')


def pcm(seconds, value=800):
    return struct.pack('<h', value) * int(16000 * seconds)


class Input:
    channels = 1
    kind = ''
    chunks = []
    fail = False
    block = False
    last = None

    def __init__(self, **kwargs):
        type(self).last = self
        self.chunks = iter(type(self).chunks)
        self.closed = threading.Event()
        self.exhausted = threading.Event()

    def open(self): return 'ТЕСТ — синтетический PCM'
    def close(self): self.closed.set()
    def read(self):
        time.sleep(.005)
        try:
            return next(self.chunks)
        except StopIteration:
            self.exhausted.set()
            if self.fail:
                raise OSError('Микрофон отключён')
            if self.block:
                self.closed.wait(5)
            return b''


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SongStore(self.root)
        self.sid = self.store.create('ТЕСТ запись')['song_id']
        self.events, self.completed = [], []
        Input.chunks, Input.fail, Input.block = [], False, False
        self.patches = [patch.object(capture, 'Capture', Input), patch.object(capture, 'LoopbackCapture', Input)]
        for p in self.patches: p.start()
        self.manager = capture.SongCaptureManager(self.root, self.store,
            lambda name, **kw: self.events.append((name, kw)),
            lambda row, **kw: self.completed.append((row, kw)))

    def tearDown(self):
        self.manager.close()
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def start(self, chunks, **kwargs):
        Input.chunks = chunks
        self.manager.start(self.sid, source='microphone', **kwargs)
        return self.manager._active[self.sid]

    def finished(self, h):
        self.assertTrue(h['done'].wait(3), self.manager.state())
        self.assertFalse(self.manager.state(self.sid)['running'])
        self.assertTrue(h['capture'].closed.is_set())
        self.assertEqual(len(self.completed), 1)
        self.assertEqual(sum(not e['running'] for _, e in self.events), 1)
        return self.store.get(self.sid)

    def test_five_seconds_silence_auto_saves_and_completes_once(self):
        h = self.start([pcm(1), *[pcm(.5, 0)] * 10])
        row = self.finished(h)
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['capture_result']['reason'], 'silence')
        self.assertEqual(row['audio']['duration_ms'], 6000)
        with wave.open(str(self.root / row['audio']['path'])) as wav:
            self.assertEqual(wav.getnframes(), 96000)
        self.manager.stop(self.sid)
        self.assertEqual(len(self.completed), 1)

    def test_sound_resets_silence_and_short_gaps_do_not_end_song(self):
        h = self.start([pcm(1), pcm(4, 0), pcm(.2), pcm(4, 0)])
        self.assertTrue(h['capture'].exhausted.wait(2))
        self.assertTrue(self.manager.state(self.sid)['running'])
        self.manager.stop(self.sid)
        self.assertEqual(self.finished(h)['audio']['duration_ms'], 9200)

    def test_silent_microphone_does_not_launch_asr_or_karaoke(self):
        h = self.start([pcm(5, 0)])
        self.assertTrue(h['capture'].exhausted.wait(2))
        self.assertEqual(self.manager.state(self.sid)['phase'],'waiting')
        self.assertEqual(self.manager.state(self.sid)['duration_ms'],0)
        self.manager.stop(self.sid)
        row = self.finished(h)
        self.assertEqual(row['status'], 'needs_input')
        self.assertFalse(row['capabilities']['audio'])
        self.assertFalse(row['audio']['path'])
        self.assertTrue((self.root / row['capture_result']['path']).exists())

    def test_waiting_silence_is_not_saved_and_onset_is_not_clipped(self):
        h=self.start([pcm(20,0),pcm(.06,900),pcm(.06,1000),pcm(1,1200)])
        self.assertTrue(h['capture'].exhausted.wait(2))
        self.manager.stop(self.sid);row=self.finished(h)
        self.assertEqual(row['audio']['duration_ms'],1360)
        with wave.open(str(self.root/row['audio']['path'])) as wav:data=wav.readframes(wav.getnframes())
        self.assertEqual(data,pcm(.24,0)+pcm(.06,900)+pcm(.06,1000)+pcm(1,1200))

    def test_short_click_does_not_start_and_preview_failure_does_not_lose_audio(self):
        self.manager.preview_factory=lambda *args:(_ for _ in ()).throw(RuntimeError('test preview failure'))
        h=self.start([pcm(.04),pcm(8,0),pcm(1)])
        self.assertTrue(h['capture'].exhausted.wait(2));self.assertEqual(h['phase'],'recording')
        self.assertIn('test preview failure',h['draft_status'])
        self.manager.stop(self.sid);self.assertEqual(self.finished(h)['audio']['duration_ms'],1300)

    def test_missing_packets_stop_and_preserve_partial_audio(self):
        self.manager.no_data_seconds = .15
        h = self.start([pcm(1.5)])
        row = self.finished(h)
        self.assertEqual(row['audio']['duration_ms'], 1500)
        self.assertEqual(row['capture_result']['reason'], 'no_data')

    def test_blocked_read_is_closed_by_watchdog(self):
        self.manager.no_data_seconds = .15
        Input.block = True
        h = self.start([pcm(1)])
        self.assertEqual(self.finished(h)['status'], 'queued')

    def test_device_error_preserves_written_audio_and_releases_input(self):
        Input.fail = True
        h = self.start([pcm(2)])
        row = self.finished(h)
        self.assertEqual(row['capture_result']['reason'], 'input_error')
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['audio']['duration_ms'], 2000)

    def test_manual_stop_race_only_completes_once(self):
        h = self.start([pcm(1)])
        self.assertTrue(h['capture'].exhausted.wait(2))
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.manager.stop(self.sid))) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join(3)
        self.finished(h)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r['audio']['duration_ms'] == 1000 for r in results))

    def test_duration_limit_and_shutdown(self):
        self.manager.max_seconds = 2
        h = self.start([pcm(1), pcm(1)])
        self.assertEqual(self.finished(h)['capture_result']['reason'], 'limit')
        self.completed.clear(); self.events.clear()
        h = self.start([pcm(1)])
        self.assertTrue(h['capture'].exhausted.wait(2))
        self.manager.close()
        self.finished(h)
        self.assertTrue(self.completed[0][1]['shutting_down'])

    def test_restart_repairs_old_raw_header_without_overwriting_audio(self):
        folder = self.root / self.sid / 'audio'
        folder.mkdir()
        path = folder / 'capture-old.wav'
        with wave.open(str(path), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(pcm(1))
        with path.open('ab') as stream: stream.write(pcm(2))
        original = path.read_bytes()
        self.store.patch(self.sid, {'status':'recording','analysis_state':'capturing',
            'audio':{'path':str(path.relative_to(self.root)), 'capture_source':'microphone'}})
        self.store.recover_interrupted()
        self.manager.recover_interrupted()
        row = self.store.get(self.sid)
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(row['audio']['duration_ms'], 3000)
        self.assertEqual(path.read_bytes(), original)
        self.assertNotEqual(row['audio']['path'], str(path.relative_to(self.root)))
        with wave.open(str(self.root / row['audio']['path'])) as wav: self.assertEqual(wav.getnframes(), 48000)
        revision = row['revision']
        self.manager.recover_interrupted()
        self.assertEqual(self.store.get(self.sid)['revision'], revision)

    def test_automatic_completion_restores_assistant_and_schedules_analysis_once(self):
        from engine_commands import CommandsMixin
        calls = []
        engine = types.SimpleNamespace(server=False, song_mic_capture_active=True,
            _song_capture_resume_muted=False, stopping=threading.Event(), song_store=self.store)
        engine.set_mute = lambda value: calls.append(('mute', value))
        def analyze(sid, full=False):
            calls.append(('analyze', sid, full))
            return self.store.patch(sid, {'status':'running', 'stage':'lyrics'})
        engine.song_analyzer = types.SimpleNamespace(start=analyze)
        self.manager.on_complete = lambda row, **kw: CommandsMixin._song_capture_finished(engine, row, **kw)
        h = self.start([pcm(1), pcm(5, 0)])
        self.assertTrue(h['done'].wait(3))
        self.assertFalse(engine.song_mic_capture_active)
        self.assertEqual(calls, [('mute', False), ('analyze', self.sid, True)])
        self.assertEqual(self.manager.stop(self.sid)['status'], 'running')
        self.assertEqual(len(calls), 2)

    def test_shutdown_restores_input_without_launching_new_worker(self):
        from engine_commands import CommandsMixin
        calls = []
        engine = types.SimpleNamespace(server=False, song_mic_capture_active=True,
            _song_capture_resume_muted=True, stopping=threading.Event(), song_store=self.store,
            song_analyzer=types.SimpleNamespace(start=lambda *a, **k: calls.append('analysis')))
        engine.set_mute = lambda value: calls.append(value)
        self.manager.on_complete = lambda row, **kw: CommandsMixin._song_capture_finished(engine, row, **kw)
        h = self.start([pcm(1)])
        self.assertTrue(h['capture'].exhausted.wait(2))
        self.manager.close()
        self.assertEqual(calls, [True])
        self.assertEqual(self.store.get(self.sid)['status'], 'queued')

    def test_restart_missing_audio_is_actionable(self):
        self.store.patch(self.sid, {'status':'interrupted','analysis_state':'capturing','audio':{'path':'missing.wav'}})
        self.manager.recover_interrupted()
        self.assertEqual(self.store.get(self.sid)['status'], 'needs_input')


if __name__ == '__main__': unittest.main()
