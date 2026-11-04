"""Изолированные проверки песен: без Engine, настроек, TTS и сетевых моделей."""
import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs.store import SongStore
from songs.analysis import SongAnalyzer
from songs.structure import infer_sections
from songs.review import review_command
from songs.chord_layout import build_guitar_sheet


class SongPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SongStore(Path(self.temp.name) / 'songs')
        self.row = self.store.create('ТЕСТ — вымышленная песня', 'Тестовый исполнитель')
        self.sid = self.row['song_id']
        self.audio = self.store.root / self.sid / 'audio' / 'test.wav'
        self.audio.parent.mkdir()
        self.audio.write_bytes(b'RIFF')
        self.store.patch(self.sid, {'audio': {'path': str(self.audio.relative_to(self.store.root))}})

    def result(self, text='Тестовая фраза для проверки', run='run-1'):
        self.store.patch(self.sid, {'status': 'running', 'active_run_id': run})
        return self.store.save_analysis(self.sid, run, [{'phrase_id': run+'-0', 'start_ms': 0,
            'end_ms': 4000, 'text': text, 'timing_quality': 'phrase', 'review_state': 'needs_review'}], 'test')

    def edit(self, row, text='Исправленная фраза', **extra):
        return self.store.edit_phrase(self.sid, row['phrases'][0]['phrase_id'], text,
            extra.pop('note', ''), extra.pop('reviewed', False), row['revision'], **extra)

    def test_analysis_preserves_user_correction_and_raw_hypotheses(self):
        row = self.edit(self.result(), reviewed=True)
        raw = copy.deepcopy(row['analysis_runs'][0])
        row = self.result('Другая ошибка модели', run='run-2')
        self.assertEqual(row['phrases'][0]['text'], 'Исправленная фраза')
        self.assertEqual(row['analysis_runs'][0], raw)
        self.assertEqual(row['analysis_runs'][1]['phrases'][0]['text'], 'Другая ошибка модели')
        self.assertEqual(row['review_state'], 'new_candidate')
        self.assertTrue(self.audio.exists())

    def test_edit_undo_redo_survive_restart(self):
        before = self.result()
        edited = self.edit(before, reviewed=True, note='sung_differently')
        self.store = SongStore(self.store.root)
        undone = self.edit(edited, action='undo')
        self.assertEqual(undone['phrases'], before['phrases'])
        redone = self.edit(undone, action='redo')
        self.assertEqual(redone['phrases'], edited['phrases'])
        self.assertEqual(redone['review_state'], 'lyrics_verified')
        self.assertNotEqual(redone['status'], 'ready')

    def test_stale_edit_cannot_overwrite(self):
        row = self.result()
        self.edit(row)
        with self.assertRaisesRegex(ValueError, 'изменилась'):
            self.edit(row, 'Устаревший текст')
        self.assertEqual(self.store.get(self.sid)['phrases'][0]['text'], 'Исправленная фраза')

    def test_text_edit_invalidates_word_alignment(self):
        row = self.result()
        row['phrases'][0].update(words=[{'text': 'test'}], timing_quality='word')
        row = self.store.patch(self.sid, {'phrases': row['phrases']})
        row = self.edit(row)
        self.assertEqual(row['phrases'][0]['words'], [])
        self.assertEqual(row['phrases'][0]['timing_quality'], 'phrase')

    def test_missing_index_recovers_cards(self):
        self.store.index_path.write_text('broken')
        self.assertEqual(self.store.list()[0]['song_id'], self.sid)

    def test_deleted_card_does_not_delete_audio_or_reappear(self):
        self.store.delete(self.sid)
        self.assertTrue(self.audio.exists())
        self.store.index_path.unlink()
        self.assertEqual(self.store.list(), [])

    def test_recovery_invalidates_old_worker(self):
        self.store.patch(self.sid, {'status': 'running', 'active_run_id': 'old'})
        self.store.recover_interrupted()
        self.assertEqual(self.store.get(self.sid)['status'], 'interrupted')
        self.assertIsNone(self.store.save_analysis(self.sid, 'old', [], 'test'))

    def test_background_analysis_is_phrase_timing_and_no_fallback(self):
        finished = threading.Event()
        class Host:
            def transcribe_file(inner, path, **kw):
                self.assertFalse(kw['allow_fallback'])
                return [(0, 2, 'Первая тестовая фраза'), (2, 4, 'Вторая тестовая фраза')]
        analyzer = SongAnalyzer(self.store.root, self.store, Host(),
            lambda event, **data: finished.set() if not data.get('running') else None)
        analyzer.start(self.sid)
        self.assertTrue(finished.wait(3))
        row = self.store.get(self.sid)
        self.assertEqual(row['status'], 'review')
        self.assertEqual(len(row['phrases']), 2)
        self.assertEqual(row['words'], [])
        self.assertEqual(row['timing_quality'], 'phrase')

    def test_cancel_late_worker_does_not_replace_text(self):
        first = self.result()
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        class Host:
            def transcribe_file(inner, path, **kw):
                entered.set()
                release.wait(3)
                return [(0, 3, 'Поздний результат')]
        analyzer = SongAnalyzer(self.store.root, self.store, Host(), lambda *a, **k: None)
        original = analyzer._run
        def run(*args):
            try: original(*args)
            finally: done.set()
        with patch.object(analyzer, '_run', side_effect=run):
            analyzer.start(self.sid)
            self.assertTrue(entered.wait(3))
            with self.assertRaisesRegex(ValueError, 'уже идёт'):
                analyzer.start(self.sid)
            analyzer.cancel(self.sid)
            release.set()
            self.assertTrue(done.wait(3))
        row = self.store.get(self.sid)
        self.assertEqual(row['status'], 'cancelled')
        self.assertEqual(row['phrases'], first['phrases'])

    def test_audio_path_escape_rejected_before_processing(self):
        row = self.result()
        self.store.patch(self.sid, {'audio': {'path': '../outside.wav'}})
        with self.assertRaisesRegex(ValueError, 'недоступна'):
            review_command(self.store, 'song_phrase_audio', {'song_id': self.sid,
                'phrase_id': row['phrases'][0]['phrase_id']})

    def test_single_repeated_line_is_not_confident_chorus(self):
        rows = [{'text': t, 'start_ms': i*1000, 'end_ms': (i+1)*1000} for i,t in enumerate(
            ['Первая строка', 'Повторяемая строка', 'Третья строка', 'Повторяемая строка'])]
        self.assertTrue(all(s['kind'] == 'unknown' for s in infer_sections(rows)))

    def test_repeated_block_is_candidate_and_preserves_empty_indexes(self):
        texts = ['', 'Длинная первая строка припева', 'Вторая строка этого припева', '',
                 'Длинная первая строка припева', 'Вторая строка этого припева']
        rows = [{'text': t, 'start_ms': i*1000, 'end_ms': (i+1)*1000} for i,t in enumerate(texts)]
        sections = infer_sections(rows)
        self.assertEqual([s['phrase_indexes'] for s in sections], [[1,2], [4,5]])
        self.assertTrue(all(s['label'] == 'Возможный припев' for s in sections))
        self.assertEqual(sections[0]['repeat_group_id'], sections[1]['repeat_group_id'])

    def test_chords_without_alignment_have_labelled_word_estimate(self):
        rows = build_guitar_sheet([{'text': 'Долгая первая гласная', 'start_ms': 0, 'end_ms': 4000}],
                                 [{'name': 'B7', 'start_ms': 2000}])
        self.assertEqual(rows[0]['chords'][0]['char_index'], 7)
        self.assertEqual(rows[0]['chords'][0]['anchor_quality'], 'estimated_phrase')
        self.assertEqual(rows[0]['unplaced_chords'], [])

    def test_chord_on_phrase_boundary_belongs_only_to_next_phrase(self):
        rows = build_guitar_sheet([{'text': 'Первая', 'start_ms': 0, 'end_ms': 2000},
                                  {'text': 'Вторая', 'start_ms': 2000, 'end_ms': 4000}],
                                 [{'name': 'Am', 'start_ms': 2000}])
        self.assertEqual(rows[0]['unplaced_chords'], [])
        self.assertEqual(rows[1]['chords'][0]['char_index'], 0)

    def test_two_chords_over_vowel_do_not_shift_anchor(self):
        phrase = {'phrase_id': 'p', 'text': 'Любовь', 'start_ms': 0, 'end_ms': 4000}
        anchor = {'phrase_id': 'p', 'kind': 'char_ctc', 'grapheme_index': 3}
        rows = build_guitar_sheet([phrase], [{'name': 'E7', 'start_ms': 1800, 'anchor': anchor},
                                             {'name': 'Am', 'start_ms': 2400, 'anchor': anchor}])
        self.assertEqual(rows[0]['cells'][1], {'text': 'овь', 'chords': ['E7','Am'], 'char_index': 3})
        self.assertEqual([e['start_ms'] for e in rows[0]['chords']], [1800,2400])

    def test_chord_in_instrumental_gap_is_not_dropped(self):
        rows = build_guitar_sheet([{'text': 'Строка', 'start_ms': 3000, 'end_ms': 5000}],
                                 [{'name': 'Em', 'start_ms': 0}])
        self.assertEqual(rows[0]['timing_quality'], 'gap')
        self.assertEqual(rows[0]['unplaced_chords'][0]['name'], 'Em')


if __name__ == '__main__':
    unittest.main()
