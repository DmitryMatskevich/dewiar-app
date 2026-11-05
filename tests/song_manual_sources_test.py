"""Manual sources use only temporary song data; no network or devices."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
from songs.store import SongStore
from songs.manual_sources import add_manual_source
from songs.public import public_song
from songs.sources import SongSources


class ManualSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SongStore(Path(self.tmp.name))
        self.row = self.store.create('Тестовая песня', 'Исполнитель')

    def add(self, url='', lyrics=''):
        current = self.store.get(self.row['song_id'])
        return add_manual_source(self.store, current['song_id'], url, lyrics, current['revision'])

    def test_manual_video_and_text_are_distinct_and_reviewable(self):
        saved = self.add('https://youtu.be/5ab3wVwfwr8', 'Одна строка песни')
        candidates = saved['source_candidates']
        self.assertEqual([c['kind'] for c in candidates], ['youtube', 'lyrics'])
        self.assertEqual(candidates[0]['url'], 'https://www.youtube.com/watch?v=5ab3wVwfwr8')
        self.assertFalse(candidates[0]['version_verified'])
        saved = self.add('https://youtu.be/5ab3wVwfwr8', 'Одна строка песни')
        self.assertEqual(len(saved['source_candidates']), 2)
        saved = self.store.patch(saved['song_id'], {'phrases':[{'phrase_id':'1','text':'Другая строка песни','start_ms':0,'end_ms':3000}]})
        self.assertTrue(public_song(saved)['review_evidence']['references'])

    def test_user_can_select_exact_recording_before_starting_batch(self):
        saved = self.add('https://youtu.be/5ab3wVwfwr8')
        candidate = saved['source_candidates'][0]
        sources = SongSources(self.store, lambda *a, **k: None)

        selected = sources.select_reference(saved['song_id'], candidate['candidate_id'])

        self.assertEqual(selected['selected_source_id'], candidate['candidate_id'])
        self.assertTrue(selected['source_candidates'][0]['selected'])
        self.assertIn('Обработать пакетно', selected['stage_label'])

    def test_existing_analysis_can_select_youtube_reference_without_replacing_it(self):
        saved = self.add('https://youtu.be/5ab3wVwfwr8')
        candidate = saved['source_candidates'][0]
        saved = self.store.patch(saved['song_id'], {
            'audio': {'path':'audio/current.wav'}, 'phrases':[{'phrase_id':'p','text':'Ручная правка'}]})
        sources = SongSources(self.store, lambda *a, **k: None)

        selected = sources.select_reference(saved['song_id'], candidate['candidate_id'])

        self.assertEqual(selected['selected_source_id'], candidate['candidate_id'])
        self.assertIn('для сверки', selected['stage_label'])
        self.assertEqual(selected['audio']['path'], 'audio/current.wav')
        self.assertEqual(selected['phrases'][0]['text'], 'Ручная правка')

    def test_new_search_retains_references_and_existing_edits(self):
        saved = self.add('https://example.org/lyrics', 'Проверочный текст')
        saved = self.store.patch(saved['song_id'], {'phrases':[{'phrase_id':'p','text':'Моя правка','start_ms':0,'end_ms':1000}],
            'audio':{'path':'kept.wav'}, 'capabilities':{'audio':True}})
        sources = SongSources(self.store, lambda *a, **k: None)
        with patch('songs.sources.discover', return_value=([], [])):
            sources.start(saved['song_id'])
            import time
            for _ in range(100):
                if not sources.running(saved['song_id']): break
                time.sleep(.01)
        result = self.store.get(saved['song_id'])
        self.assertEqual(result['phrases'][0]['text'], 'Моя правка')
        self.assertEqual(result['audio']['path'], 'kept.wav')
        self.assertEqual(len(result['source_candidates']), 2)

    def test_processing_selected_recording_starts_full_analysis_after_download(self):
        saved = self.add('https://youtu.be/5ab3wVwfwr8')
        candidate = saved['source_candidates'][0]
        calls = []
        class Analyzer:
            def start(_, song_id, full=False): calls.append((song_id, full))
        def download(url, folder, cancel):
            audio = folder / 'recording.wav'
            audio.write_bytes(b'wave-fixture')
            return audio, {'duration': 171, 'title': 'Тестовая запись'}
        sources = SongSources(self.store, lambda *a, **k: None, Analyzer())
        with patch('media_transcription._audio', side_effect=download):
            sources.start(saved['song_id'], candidate['candidate_id'], process=True)
            import time
            for _ in range(200):
                if calls and not sources.running(saved['song_id']): break
                time.sleep(.01)
        self.assertEqual(calls, [(saved['song_id'], True)])
        result = self.store.get(saved['song_id'])
        self.assertEqual(result['status'], 'queued')  # the real analyzer owns the next transition
        self.assertEqual(result['audio']['duration_ms'], 171000)

    def test_invalid_conflicting_and_busy_inputs_do_not_mutate(self):
        for url in ['file:///etc/passwd', 'https://user:password@example.org', 'javascript:alert(1)']:
            with self.assertRaises(ValueError): self.add(url)
        with self.assertRaises(ValueError):
            add_manual_source(self.store, self.row['song_id'], 'https://example.org', '', 999)
        self.store.patch(self.row['song_id'], {'status':'running'})
        with self.assertRaises(ValueError): self.add('https://example.org')
        self.assertFalse(self.store.get(self.row['song_id']).get('source_candidates'))


if __name__ == '__main__':
    unittest.main()
