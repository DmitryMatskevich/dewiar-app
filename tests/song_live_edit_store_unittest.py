"""Persistence and timing safety for an inline live word edit."""
import tempfile
import unittest
from pathlib import Path

from core.songs.store import SongStore


class LiveWordEditStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dewiar-live-word-store-')
        self.store = SongStore(Path(self.temp.name) / 'songs')
        row = self.store.create('Песня для теста live edit')
        self.song_id = row['song_id']
        phrase = {
            'phrase_id': 'phrase-1', 'start_ms': 0, 'end_ms': 5000,
            'text': 'Я пою сейчас', 'review_state': 'needs_review',
            'words': [
                {'text': 'Я', 'char_start': 0, 'char_end': 1, 'start_ms': 0, 'end_ms': 500},
                {'text': 'пою', 'char_start': 2, 'char_end': 5, 'start_ms': 500, 'end_ms': 1800},
                {'text': 'сейчас', 'char_start': 6, 'char_end': 12, 'start_ms': 1800, 'end_ms': 3500},
            ],
        }
        chords = [
            {'name': 'Am', 'start_ms': 0, 'end_ms': 1500,
             'anchor': {'phrase_id': 'phrase-1', 'grapheme_index': 0}, 'verified': True},
            {'name': 'E7', 'start_ms': 1800, 'end_ms': 3500,
             'anchor': {'phrase_id': 'phrase-1', 'grapheme_index': 6}, 'verified': True},
        ]
        self.row = self.store.patch(self.song_id, {'phrases': [phrase], 'chords': chords,
                                                   'capabilities': {'lyrics': True, 'chords': True}})

    def tearDown(self):
        self.temp.cleanup()

    def test_word_edit_keeps_timing_and_invalidates_later_chord_anchor(self):
        row = self.store.edit_phrase(self.song_id, 'phrase-1', 'играю', '', False,
                                     self.row['revision'], word_start=2)
        phrase = row['phrases'][0]
        self.assertEqual(phrase['text'], 'Я играю сейчас')
        self.assertEqual(phrase['words'][1]['text'], 'играю')
        self.assertEqual((phrase['words'][2]['char_start'], phrase['words'][2]['char_end']), (8, 14))
        self.assertEqual((phrase['words'][2]['start_ms'], phrase['words'][2]['end_ms']), (1800, 3500))
        self.assertEqual(row['chords'][0]['anchor']['grapheme_index'], 0)
        self.assertNotIn('anchor', row['chords'][1])
        self.assertEqual(row['chords'][1]['previous_anchor']['grapheme_index'], 6)
        self.assertFalse(row['chords'][1]['verified'])
        self.assertEqual(self.store.get(self.song_id)['phrases'][0]['text'], 'Я играю сейчас')

    def test_word_edit_rejects_stale_revision(self):
        with self.assertRaisesRegex(ValueError, 'уже изменилась'):
            self.store.edit_phrase(self.song_id, 'phrase-1', 'играю', '', False,
                                   self.row['revision'] - 1, word_start=2)

    def test_estimated_display_word_keeps_its_playback_cue_when_edited(self):
        row = self.store.patch(self.song_id, {'phrases': [{
            'phrase_id': 'phrase-1', 'start_ms': 0, 'end_ms': 5000,
            'text': 'Старый текст', 'words': [], 'review_state': 'needs_review'}], 'chords': []})
        edited = self.store.edit_phrase(self.song_id, 'phrase-1', 'Новый', '', False,
                                        row['revision'], word_start=0,
                                        word_start_ms=300, word_end_ms=1100)
        phrase = edited['phrases'][0]
        self.assertEqual(phrase['text'], 'Новый текст')
        self.assertEqual(phrase['words'], [{'text': 'Новый', 'char_start': 0, 'char_end': 5,
            'start_ms': 300, 'end_ms': 1100, 'method': 'manual_word_edit_estimated',
            'verified': True}])
        from core.songs.playback_layout import display_words
        first = display_words(phrase)[0]
        self.assertEqual((first['start_ms'], first['end_ms'], first['quality']), (300, 1100, 'estimated_manual'))


if __name__ == '__main__':
    unittest.main()
