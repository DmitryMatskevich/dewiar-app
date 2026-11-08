"""Temporary song stores and stubbed workers: no models, downloads or user data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs.store import SongStore
from songs.analysis import SongAnalyzer
from songs.processing import SongProcessing
from songs.public import public_song


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SongStore(Path(self.tmp.name))
        self.sid = self.store.create('ТЕСТ продолжения')['song_id']
        self.analyzer = SongAnalyzer(self.store.root, self.store, Mock(), Mock())
        self.sources = Mock()
        self.sources.running.return_value = False
        self.capture = Mock()
        self.capture.state.return_value = {'running': False}
        self.service = SongProcessing(self.store, self.analyzer, self.sources, self.capture)

    def audio(self, status='partial'):
        audio = self.store.root / self.sid / 'audio' / 'test.wav'
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b'fixture')
        return self.store.patch(self.sid, {'status': status, 'audio': {'path': str(audio.relative_to(self.store.root))}, 'capabilities': {'audio': True}})

    def checkpoint(self, complete=False):
        self.audio('interrupted')
        self.store.patch(self.sid, {'interrupted_run_id': 'old'})
        folder = self.store.root / self.sid / 'analysis' / 'old'
        folder.mkdir(parents=True)
        (folder/'gigaam.json').write_text(json.dumps({'model': 'test', 'phrases': []}))
        (folder/'result.json').write_text(json.dumps({'hypotheses': [], 'completed': complete}))
        (folder/'result.pid').write_text('123')
        return folder

    def test_resume_metadata_survives_card_projection_without_archive(self):
        row = self.audio()
        row['processing'] = self.service.describe(row)
        card = public_song(row, summary=True)
        self.assertTrue(card['processing']['can_resume'])
        self.assertNotIn('audio', card)
        self.assertNotIn('source_candidates', card)

    def test_partial_and_failed_and_cancelled_audio_can_resume(self):
        for status in ('partial', 'failed', 'cancelled', 'queued', 'needs_input'):
            self.audio(status)
            with patch.object(self.analyzer, 'start') as start:
                self.service.resume(self.sid)
                start.assert_called_once_with(self.sid, full=True)

    def test_ready_song_not_reprocessed(self):
        row = self.audio('ready')
        self.store.patch(self.sid, {'capabilities': {'lyrics': True, 'chords': True, 'audio': True}})
        with self.assertRaises(ValueError): self.service.resume(self.sid)

    def test_explicit_restart_ready_song_uses_saved_audio_without_erasing_edits(self):
        self.audio('ready')
        row=self.store.patch(self.sid,{'capabilities':{'lyrics':True,'chords':True,'audio':True},
            'phrase_history':{'p':{'cursor':0,'versions':[{'text':'Ручная правка'}]}}})
        self.assertTrue(self.service.describe(row)['can_restart'])
        with patch.object(self.analyzer,'start',return_value=row) as start:
            self.service.restart(self.sid);start.assert_called_once_with(self.sid,full=True)
        self.assertEqual(self.store.get(self.sid)['phrase_history'],row['phrase_history'])

    def test_restart_unavailable_during_capture(self):
        row=self.audio('recording')
        self.capture.state.return_value={'running':True,'phase':'waiting','duration_ms':0,'draft':''}
        info=self.service.describe(row);self.assertFalse(info['can_restart'])
        self.assertEqual(info['capture']['phase'],'waiting')
        with patch.object(self.analyzer,'start') as start:
            self.service.restart(self.sid);start.assert_not_called()

    def test_busy_repeat_does_not_create_duplicate(self):
        self.audio('running')
        self.analyzer._running.add(self.sid)
        with patch.object(self.analyzer, 'start') as start:
            self.assertEqual(self.service.resume(self.sid)['status'], 'running')
            start.assert_not_called()
        self.sources.start.assert_not_called()

    def test_selected_recording_without_audio(self):
        row = self.store.patch(self.sid, {'status': 'failed', 'selected_source_id': 'chosen',
            'source_candidates': [{'candidate_id': 'chosen', 'kind': 'youtube'}]})
        self.assertTrue(self.service.describe(row)['can_resume'])
        self.service.resume(self.sid)
        self.sources.start.assert_called_once_with(self.sid, 'chosen', process=True)

    def test_missing_audio_cannot_silently_replace_existing_recording(self):
        self.audio()
        (self.store.root / self.sid / 'audio' / 'test.wav').unlink()
        self.store.patch(self.sid, {'selected_source_id': 'chosen', 'source_candidates': [{'candidate_id': 'chosen', 'kind': 'youtube'}]})
        with self.assertRaises(ValueError): self.service.resume(self.sid)
        self.sources.start.assert_not_called()

    def test_active_detached_worker_checked_before_partial_file(self):
        self.checkpoint()
        with patch.object(self.analyzer, '_worker_running', return_value=True), patch('songs.analysis.threading.Thread') as thread, patch.object(self.analyzer, '_restore_and_save') as save:
            self.service.resume(self.sid)
            save.assert_not_called()
            thread.return_value.start.assert_called_once()
            self.assertTrue(self.analyzer.running(self.sid))

    def test_dead_checkpoint_restarts_instead_of_publishing_partial_as_finished(self):
        self.checkpoint()
        with patch.object(self.analyzer, '_worker_running', return_value=False), patch.object(self.analyzer, 'start') as start, patch.object(self.analyzer, '_restore_and_save') as save:
            self.service.resume(self.sid)
            start.assert_called_once_with(self.sid, full=True)
            save.assert_not_called()

    def test_completed_worker_saved_without_rerunning(self):
        self.checkpoint(complete=True)
        with patch.object(self.analyzer, '_worker_running', return_value=False), patch.object(self.analyzer, 'start') as start:
            self.service.resume(self.sid)
            start.assert_not_called()
            self.assertEqual(len(self.store.get(self.sid)['analysis_runs']), 1)

    def test_queued_audio_recovers_at_startup_but_cancelled_does_not(self):
        self.audio('queued')
        with patch.object(self.analyzer, 'start') as start:
            self.analyzer.resume_interrupted()
            start.assert_called_once_with(self.sid, full=True)
        self.store.patch(self.sid, {'status': 'cancelled'})
        with patch.object(self.analyzer, 'start') as start:
            self.analyzer.resume_interrupted()
            start.assert_not_called()

    def test_stale_running_without_worker_can_be_resumed(self):
        self.audio('running')
        with patch.object(self.analyzer, 'start') as start:
            self.service.resume(self.sid)
            start.assert_called_once_with(self.sid, full=True)

    def test_resume_keeps_previously_selected_song_models(self):
        self.audio('interrupted')
        self.store.patch(self.sid,{'analysis_models':['whisper','qwen']})
        with patch('songs.analysis.threading.Thread'):
            row=self.analyzer.start(self.sid,full=True)
        self.assertEqual(row['analysis_models'],['whisper','qwen'])

    def test_old_card_can_recover_model_choice_from_recorded_change(self):
        self.audio('interrupted')
        self.store.patch(self.sid,{'analysis_models':['whisper','qwen']})
        path=self.store.root/self.sid/'song.json'
        old=json.loads(path.read_text());old.pop('analysis_models');path.write_text(json.dumps(old))
        with patch('songs.analysis.threading.Thread'):
            row=self.analyzer.start(self.sid,full=True)
        self.assertEqual(row['analysis_models'],['whisper','qwen'])


if __name__ == '__main__': unittest.main()
