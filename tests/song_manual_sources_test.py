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
