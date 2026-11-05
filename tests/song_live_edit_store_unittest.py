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

    def test_editing_joined_display_line_includes_both_timed_phrase_fragments(self):
        first = {'phrase_id': 'phrase-a', 'start_ms': 0, 'end_ms': 2000,
                 'text': 'Перед ним напрочь', 'review_state': 'needs_review',
                 'words': [{'text': 'Перед', 'char_start': 0, 'char_end': 5,
                            'start_ms': 0, 'end_ms': 500}]}
        second = {'phrase_id': 'phrase-b', 'start_ms': 2020, 'end_ms': 3200,
                  'text': 'цельный четвертак.', 'review_state': 'needs_review',
                  'words': [{'text': 'четвертак.', 'char_start': 8, 'char_end': 18,
                             'start_ms': 2500, 'end_ms': 3100}]}
        row = self.store.patch(self.song_id, {'phrases': [first, second], 'chords': [],
                                              'audio': {'duration_ms': 4000}})
        from core.songs.public import public_song
        line = next(item for item in public_song(row)['guitar_sheet']
                    if item.get('text', '').endswith('напрочь цельный четвертак.'))
        self.assertEqual(line['text'], 'Перед ним напрочь цельный четвертак.')
        self.assertEqual([span['phrase_id'] for span in line['source_spans']], ['phrase-a', 'phrase-b'])

        edited = self.store.edit_display_line(self.song_id, line['phrase_id'],
            'Перед ним напрочь цельной четверта.', False, row['revision'])
        by_id = {phrase['phrase_id']: phrase for phrase in edited['phrases']}
        self.assertEqual(by_id['phrase-a']['text'], 'Перед ним напрочь')
        self.assertEqual(by_id['phrase-b']['text'], 'цельной четверта.')
        self.assertEqual((by_id['phrase-a']['start_ms'], by_id['phrase-a']['end_ms']), (0, 2000))
        self.assertEqual((by_id['phrase-b']['start_ms'], by_id['phrase-b']['end_ms']), (2020, 3200))
        self.assertEqual(by_id['phrase-a']['words'][0]['text'], 'Перед')
        self.assertEqual(by_id['phrase-b']['words'], [])
        self.assertIn('phrase-b', edited['phrase_history'])

    def test_editing_joined_display_line_rejects_stale_revision(self):
        from core.songs.public import public_song
        row = self.store.patch(self.song_id, {'phrases': [
            {'phrase_id': 'phrase-a', 'start_ms': 0, 'end_ms': 1000, 'text': 'до конца'},
            {'phrase_id': 'phrase-b', 'start_ms': 1020, 'end_ms': 2000, 'text': 'строки.'}],
            'chords': [], 'audio': {'duration_ms': 2500}})
        line = next(item for item in public_song(row)['guitar_sheet'] if item.get('source_spans') and len(item['source_spans']) > 1)
        with self.assertRaisesRegex(ValueError, 'Карточка уже изменилась'):
            self.store.edit_display_line(self.song_id, line['phrase_id'], 'до конца строки.', False,
                                         row['revision'] - 1)


if __name__ == '__main__':
    unittest.main()
