"""Intro, breaks and edges from the vocal stem — synthetic signals only.

Regression cases from the library: a short first word stretched over the
intro («То», 0→13 s), a first phrase at 0 s with the voice entering at 23 s,
a last word held through a break, a voiced gap called «Проигрыш», and a rerun
which merged new timings into the card by list position.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs import vocal_timing as vt
from songs.chord_layout import build_guitar_sheet


def words(text, times):
    import re
    tokens = list(re.finditer(r'\S+', text))
    assert len(tokens) == len(times)
    return [dict(text=t.group(), char_start=t.start(), char_end=t.end(), start_ms=a, end_ms=b,
                 method='qwen3_forced_aligner') for t, (a, b) in zip(tokens, times)]


def phrase(pid, text, a, b, times=None, **extra):
    row = dict(phrase_id=pid, text=text, start_ms=a, end_ms=b, **extra)
    if times:
        row.update(words=words(text, times), timing_quality='word_estimated', alignment_stale=False)
    else:
        row.update(words=[], timing_quality='phrase')
    return row


def stem(voiced, seconds, sr=16000, seed=1):
    """Leakage noise everywhere, a sung (harmonic, vibrato) voice in ``voiced``."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    x = rng.normal(0, .0015, len(t))
    f0 = 220 * (1 + .01 * np.sin(2 * np.pi * 5 * t))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    voice = sum(np.sin(k * phase) / k for k in range(1, 6)) * .2
    for a, b in voiced:
        x[int(a * sr):int(b * sr)] += voice[int(a * sr):int(b * sr)]
    return x.astype(np.float32), sr


class VocalActivityTests(unittest.TestCase):
    def test_intervals_follow_voice_and_ignore_breaths(self):
        x, sr = stem([(10, 20), (20.25, 24), (30, 40)], 45)
        act = vt.activity(vt.envelope(x, sr), 45000)
        got = act['intervals']
        self.assertEqual(len(got), 2, got)  # 250 ms breath is bridged
        self.assertLess(abs(got[0][0] - 10000), 120)
        self.assertLess(abs(got[0][1] - 24000), 120)
        self.assertLess(abs(got[1][0] - 30000), 120)

    def test_flat_stem_is_not_used(self):
        x = np.random.default_rng(2).normal(0, .05, 16000 * 30).astype(np.float32)
        self.assertIsNone(vt.activity(vt.envelope(x, 16000), 30000))
        rows = [phrase('p', 'Одна строка', 0, 5000)]
        fitted, report = vt.fit_phrases(rows, None)
        self.assertEqual(fitted, rows)
        self.assertFalse(report['applied'])


class FitTests(unittest.TestCase):
    VOICE = {'intervals': [[12600, 20000], [24000, 31000], [40000, 41000], [50000, 60000]]}

    def test_short_first_word_stretched_over_intro(self):
        p = phrase('p0', 'То ли ветер то ли дождь', 0, 15000,
                   [(0, 13000), (13050, 13400), (13400, 13900), (13900, 14200), (14200, 14400), (14400, 15000)])
        fitted, report = vt.fit_phrases([p], self.VOICE)
        self.assertEqual(fitted[0]['start_ms'], 12540)
        self.assertEqual(fitted[0]['words'][0]['start_ms'], 12540)
        self.assertEqual(fitted[0]['words'][0]['measured_start_ms'], 0)
        self.assertEqual(fitted[0]['text'], p['text'])
        self.assertTrue(report['applied'])

    def test_first_phrase_at_zero_without_word_times(self):
        voice = {'intervals': [[23460, 30000], [33000, 40000]]}
        rows = [phrase('p0', 'Человек хороший', 0, 30000), phrase('p1', 'дальше', 33000, 40000)]
        fitted, _ = vt.fit_phrases(rows, voice)
        self.assertEqual(fitted[0]['start_ms'], 23400)
        self.assertEqual(fitted[0]['measured_start_ms'], 0)
        self.assertEqual(fitted[1]['start_ms'], 33000)

    def test_last_word_held_through_a_break(self):
        p = phrase('p1', 'Светлый день', 39000, 47000, [(39000, 39900), (40000, 47000)])
        fitted, _ = vt.fit_phrases([p], self.VOICE)
        self.assertEqual(fitted[0]['end_ms'], 41150)
        self.assertEqual(fitted[0]['words'][-1]['end_ms'], 41150)

    def test_normal_sustained_word_is_kept(self):
        p = phrase('p2', 'Долгий звук', 50000, 58000, [(50000, 51000), (51000, 58000)])
        fitted, report = vt.fit_phrases([p], self.VOICE)
        self.assertEqual(fitted[0]['words'], p['words'])
        self.assertFalse(report['applied'])

    def test_manual_boundaries_are_not_moved(self):
        p = phrase('p0', 'Ручная граница', 0, 20000, timing_quality='manual_phrase')
        p['timing_quality'] = 'manual_phrase'
        fitted, report = vt.fit_phrases([p], self.VOICE)
        self.assertEqual(fitted[0]['start_ms'], 0)
        self.assertFalse(report['applied'])

    def test_phrase_without_voice_is_flagged_not_removed(self):
        # «Валерий Курас Светлый день»: recognizer text over a silent stem.
        p = phrase('p9', 'Валерий Курас Светлый день', 160, 10000,
                   [(160, 640), (640, 1600), (5300, 6000), (6000, 10000)])
        fitted, report = vt.fit_phrases([p], self.VOICE)
        self.assertEqual(fitted[0]['text'], p['text'])
        self.assertEqual(fitted[0]['words'], p['words'])
        self.assertEqual(fitted[0]['vocal_evidence'], 'none')
        # Quiet singing above the weak threshold is not «no voice».
        quiet = dict(self.VOICE, weak=[[100, 9000]])
        self.assertNotIn('vocal_evidence', vt.fit_phrases([p], quiet)[0][0])


class GapTests(unittest.TestCase):
    VOICE = {'intervals': [[0, 500], [12000, 13000], [13400, 16000], [30000, 31000], [33000, 34000]],
             'lexical': [[10000, 17000]]}

    def test_kinds(self):
        # Singing with words a recognizer heard there → not a break.
        self.assertEqual(vt.split_gap(self.VOICE, 10000, 20000),
                         [(10000, 12000, 'pause'), (12000, 16000, 'vocal'), (16000, 20000, 'break')])
        # The same voice without lexical support is a lead instrument.
        plain = {'intervals': self.VOICE['intervals']}
        self.assertEqual(vt.split_gap(plain, 10000, 20000), [(10000, 20000, 'break')])
        # A short gap between two lines is a pause, even with a breath in it.
        self.assertEqual(vt.split_gap(plain, 29500, 31500), [(29500, 31500, 'pause')])
        # Silence ≥ 3 s is a break; voice around it does not make it a pause.
        self.assertEqual(vt.split_gap(plain, 30000, 37500), [(30000, 37500, 'break')])
        # A stem «voiced» for most of a long gap without words: an instrument solo.
        solo = {'intervals': [[40000, 52000]]}
        self.assertEqual(vt.split_gap(solo, 40000, 52000), [(40000, 52000, 'break')])
        # Song start/end are always breaks when they last a second or more.
        self.assertEqual(vt.split_gap(plain, 0, 1500), [(0, 1500, 'break')])
        self.assertEqual(vt.split_gap(plain, 58000, 60000, 60000), [(58000, 60000, 'break')])
        self.assertEqual(vt.split_gap(None, 0, 5000), [(0, 5000, 'break')])

    def test_lexical_windows_ignore_spelling_noise(self):
        rows = [phrase('a', 'один два три четыре пять шесть семь восемь', 0, 20000)]
        noisy = [{'model': 'Qwen', 'phrases': [{'text': 'один два три четыре пять шест семь восемь',
                                                 'start_ms': 0, 'end_ms': 25000}]}]
        missing = [{'model': 'Qwen', 'phrases': [{'text': 'давай наливай поговорим будущий день',
                                                   'start_ms': 25000, 'end_ms': 50000}]}]
        self.assertEqual(vt.unrecognized_windows(rows, noisy), [])
        self.assertEqual(vt.unrecognized_windows(rows, missing), [[25000, 50000]])

    def test_sheet_keeps_intro_chords_and_marks_unrecognized_singing(self):
        voice = {'intervals': [[20000, 30000], [40000, 50000], [60000, 70000]], 'lexical': [[38000, 52000]]}
        rows = [phrase('a', 'Первая строка', 20000, 30000), phrase('b', 'Вторая строка', 60000, 70000),
                phrase('x', 'Валерий Курас', 1000, 6000, vocal_evidence='none')]
        chords = [{'name': 'Am7', 'start_ms': 0, 'end_ms': 10000}, {'name': 'D6', 'start_ms': 10000, 'end_ms': 20000},
                  {'name': 'G', 'start_ms': 20000, 'end_ms': 45000}, {'name': 'Em', 'start_ms': 45000, 'end_ms': 70000}]
        sheet = build_guitar_sheet(rows, chords, 72000, voice)
        self.assertNotIn('Валерий Курас', [r.get('text') for r in sheet])
        gaps = [r for r in sheet if r.get('instrumental')]
        intro = gaps[0]
        self.assertEqual((intro['start_ms'], intro['end_ms']), (0, 20000))
        self.assertEqual([c['name'] for c in intro['unplaced_chords']], ['Am7', 'D6'])
        self.assertEqual(intro['unsung'], [{'phrase_id': 'x', 'text': 'Валерий Курас'}])
        kinds = [(g['start_ms'], g['end_ms'], 'vocal' if g.get('vocal') else 'pause' if g.get('pause') else 'break')
                 for g in gaps[1:]]
        self.assertEqual(kinds, [(30000, 40000, 'break'), (40000, 50000, 'vocal'), (50000, 60000, 'break'),
                                 (70000, 72000, 'break')])
        # Without a vocal map the old layout is unchanged.
        old = build_guitar_sheet(rows[:2], chords, 70000)
        self.assertEqual([(g['start_ms'], g['end_ms']) for g in old if g.get('instrumental')], [(0, 20000), (30000, 60000)])


class ChordLineTests(unittest.TestCase):
    """«Гоп со смыком»: every sung line carries its chord."""

    def test_each_line_has_a_chord_and_overlaps_do_not_swallow_changes(self):
        from songs.sheet_lines import readable_sheet
        rows = [phrase('big', 'Девки жены и бабули все кто хочет', 20000, 90000),  # broken, 70 s
                phrase('a', 'Как-то на трамвайной остановке', 31000, 33600),
                phrase('b', 'Встретил он Тамарку', 35000, 38000),
                phrase('c', 'Лопнула зипуха на морковке', 43000, 45400)]
        chords = [{'name': 'Am', 'start_ms': 20000, 'end_ms': 31500}, {'name': 'B', 'start_ms': 31500, 'end_ms': 35500},
                  {'name': 'Em', 'start_ms': 35500, 'end_ms': 90000}]
        sheet = readable_sheet(build_guitar_sheet(rows, chords, 90000), events=chords)
        lines = {r['text']: [m['name'] for m in r['chords']] for r in sheet if r.get('text')}
        # Changes inside the short lines stay with them, not with the broken
        # 70-second line around them.
        self.assertIn('B', lines['Как-то на трамвайной остановке'])
        self.assertIn('Em', lines['Встретил он Тамарку'])
        self.assertNotIn('B', lines['Девки жены и бабули все кто хочет'])
        # No change inside the line: the sounding chord is repeated over it.
        self.assertEqual(lines['Лопнула зипуха на морковке'], ['Em'])
        self.assertTrue(all(lines.values()))


class MergeTests(unittest.TestCase):
    def test_rerun_with_a_dropped_phrase_does_not_shift_neighbours(self):
        current = [phrase('a', 'Первая строка', 0, 5000), phrase('b', 'Вторая строка', 5000, 9000),
                   phrase('c', 'Третья строка', 9000, 12000)]
        fresh = [phrase('x', 'Первая строка', 1000, 4000), phrase('z', 'Третья строка', 9500, 11500)]
        merged, matched = vt.merge_timing(current, fresh)
        self.assertEqual(matched, 2)
        self.assertEqual([(p['phrase_id'], p['start_ms'], p['end_ms']) for p in merged],
                         [('a', 1000, 4000), ('b', 5000, 9000), ('c', 9500, 11500)])

    def test_user_text_kept_and_only_edges_move_for_same_id(self):
        current = [phrase('a', 'Исправлено мной', 0, 5000)]
        fresh = [phrase('a', 'распознано иначе', 2000, 5000, [(2000, 3000), (3000, 5000)])]
        merged, _ = vt.merge_timing(current, fresh)
        self.assertEqual(merged[0]['text'], 'Исправлено мной')
        self.assertEqual((merged[0]['start_ms'], merged[0]['words']), (2000, []))


class RecoverySungGapTests(unittest.TestCase):
    """«Давай, наливай»: Whisper invented credits over the intro and lost the
    opening chorus; Qwen heard it. A fresh two-model reading fills it in."""

    def setUp(self):
        import types
        fake = types.ModuleType('soundfile')
        fake.info = lambda path: types.SimpleNamespace(duration=60.0)
        self._saved = sys.modules.get('soundfile')
        sys.modules['soundfile'] = fake
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core' / 'songs'))
        import importlib
        self.worker = importlib.import_module('songs.worker')
        self.worker.release_qwen_model = lambda: None

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop('soundfile', None)
        else:
            sys.modules['soundfile'] = self._saved

    def rows(self):
        return [phrase('h', 'Валерий Курас Светлый день', 160, 10000, vocal_evidence='none'),
                phrase('k', 'Кто повторит?', 29600, 29920, [(29600, 29680), (29680, 29920)])]

    ACTIVITY = {'intervals': [[13900, 15900], [17900, 19700], [21600, 27400], [29000, 31100]],
                'weak': [[13900, 15900], [17900, 19700], [21600, 27400], [29000, 31100]]}
    CONTEXT = [{'model': 'Qwen3-ASR-1.7B', 'phrases': [
        {'text': 'Давай наливай, поговорим. Будущий день покажется светлым.', 'start_ms': 0, 'end_ms': 25000,
         'timing_quality': 'chunk'}]}]

    def align(self, text, a, b):
        tokens = text.split()
        step = (27000 - 14000) // len(tokens)
        return words(text, [(14000 + i * step, 14000 + (i + 1) * step - 50) for i in range(len(tokens))])

    def test_two_models_agree_and_line_is_inserted(self):
        hyp = {'phrases': self.rows()}
        def transcribe(a, b, index):
            self.assertEqual((a, b), (13100, 28200))
            return ('Давай, наливай, поговорим. Будущий день покажется светлым, как снова',
                    'давай наливай поговорим будущий день покажется светлым', ['Whisper', 'Qwen'])
        added = self.worker.recover_sung_gaps('x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None,
                                              transcribe=transcribe, align=self.align, run_id='r')
        self.assertEqual(added, 1)
        texts = [p['text'] for p in hyp['phrases']]
        self.assertEqual(texts, ['Валерий Курас Светлый день',
                                 'Давай, наливай, поговорим. Будущий день покажется светлым,', 'Кто повторит?'])
        new = hyp['phrases'][1]
        self.assertEqual(new['recovered']['method'], 'two_model_sung_gap')
        self.assertEqual(new['review_state'], 'needs_review')
        self.assertEqual(hyp['phrases'][2], self.rows()[1])

    def test_disagreement_is_settled_by_the_repeated_chorus(self):
        # Whisper «набивай», Qwen «гори, давай»: the chorus returns later and
        # was recognized there; both readings support that line.
        rows = self.rows() + [phrase('c', 'Давай, наливай, поговорим Будущий день покажется светлым,', 81440, 92480),
                              phrase('d', 'Как снова лилий.', 93000, 93880)]
        hyp = {'phrases': rows}
        added = self.worker.recover_sung_gaps(
            'x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None, run_id='r', align=self.align,
            transcribe=lambda a, b, i: ('Давай, набивай! Поговорим! Будущий день покажется светлым, Как снова верим!',
                                        'Давай гори, давай поговорим. Будущий день покажется светлым, как с добрыми.',
                                        ['W', 'Q']))
        self.assertEqual(added, 1)
        new = next(p for p in hyp['phrases'] if p.get('recovered'))
        self.assertEqual(new['text'], 'Давай, наливай, поговорим Будущий день покажется светлым, Как снова')
        self.assertEqual(new['recovered']['basis'], 'two_models_and_repeated_line')
        # A neighbouring line is the same occurrence, not a repeat.
        self.assertEqual(vt.repeated_line('Папа не хромает, просто так идти', 'Папа не хромает, просто так и.',
                                          [phrase('p', 'Марлевым буду бинтом Папа не хромает', 50000, 62000),
                                           phrase('q', 'просто так идёт Колыбель качает', 75300, 99800)],
                                          67800, 75200), '')

    def test_mostly_aligned_line_is_kept_as_partial(self):
        hyp = {'phrases': self.rows()}
        def align(text, a, b):
            tokens = text.split()
            full = self.align(text, a, b)
            return full[:len(tokens) - 1]  # the aligner lost the last word
        added = self.worker.recover_sung_gaps(
            'x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None, run_id='r', align=align,
            transcribe=lambda a, b, i: ('давай наливай поговорим будущий день покажется светлым',
                                        'давай наливай поговорим будущий день покажется светлым', ['W', 'Q']))
        self.assertEqual(added, 1)
        new = hyp['phrases'][1]
        self.assertEqual(new['timing_quality'], 'word_partial')
        self.assertEqual(new['end_ms'], 27400)  # the unmeasured tail ends with the voice
        self.assertIn('alignment_warning', new)

    def test_disagreement_or_misplaced_words_add_nothing(self):
        hyp = {'phrases': self.rows()}
        added = self.worker.recover_sung_gaps(
            'x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None, run_id='r',
            transcribe=lambda a, b, i: ('Субтитры сделал DimaTorzok', 'давай наливай поговорим', ['W', 'Q']),
            align=self.align)
        self.assertEqual(added, 0)
        self.assertEqual(hyp['phrases'], self.rows())
        self.assertEqual(hyp['sung_gap_recovery']['windows'][0]['reason'], 'models_disagree')
        late = lambda text, a, b: words(text, [(40000 + i * 300, 40250 + i * 300) for i in range(len(text.split()))])
        hyp = {'phrases': self.rows()}
        added = self.worker.recover_sung_gaps(
            'x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None, run_id='r',
            transcribe=lambda a, b, i: ('давай наливай поговорим будущий', 'давай наливай поговорим будущий', ['W', 'Q']),
            align=late)
        self.assertEqual(added, 0)
        self.assertEqual(hyp['sung_gap_recovery']['windows'][0]['reason'], 'alignment_outside_voice')



class GridRealignTests(unittest.TestCase):
    """«Давай, наливай»: Whisper wrote 30–32, 32–34, 34–36 … s; every line was
    aligned in its own two-second crop and the verse ran one line early
    («Прямо сейчас» at 33 s, sung at 36.5 s)."""

    ACTIVITY = {'intervals': [[28960, 31080], [32940, 34880], [36540, 42140], [43920, 46040]],
                'weak': [[28960, 31080], [32940, 34880], [36540, 42140], [43920, 46040]]}
    TEXTS = ['Кто повторит?', 'Прямо сейчас', 'Пусть будут эти', 'Снова лилии', 'А где мой покой?']
    CONTEXT = [{'model': 'Whisper-large-v2-MLX', 'phrases': [
        {'text': t, 'start_ms': 30000 + 2000 * i, 'end_ms': 32000 + 2000 * i} for i, t in enumerate(TEXTS)]},
        {'model': 'Qwen3-ASR-1.7B', 'phrases': [{'text': 'x', 'start_ms': 25000, 'end_ms': 50000,
                                                  'timing_quality': 'chunk'}]}]
    SUNG = [(29000, 29400), (33000, 33900), (36560, 37000), (37080, 38100), (38600, 38840), (38840, 39080),
            (39320, 39960), (40200, 40900), (41000, 41400), (43960, 44100), (44200, 44440), (44440, 44600),
            (44920, 45640)]

    def setUp(self):
        import types
        fake = types.ModuleType('soundfile')
        fake.info = lambda path: types.SimpleNamespace(duration=60.0)
        self._saved = sys.modules.get('soundfile')
        sys.modules['soundfile'] = fake
        import importlib
        self.worker = importlib.import_module('songs.worker')
        self.worker.release_qwen_model = lambda: None

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop('soundfile', None)
        else:
            sys.modules['soundfile'] = self._saved

    def rows(self):
        return [phrase('k', 'Кто повторит?', 29600, 29920, [(29600, 29680), (29680, 29920)]),
                phrase('p', 'Прямо сейчас', 32960, 33920, [(32960, 33200), (33600, 33920)]),
                phrase('u', 'Пусть будут эти', 34000, 36000),
                phrase('s', 'Снова лилии', 36000, 37440, [(36000, 37040), (37040, 37440)]),
                phrase('g', 'А где мой покой?', 38000, 40000),
                phrase('n', 'Ну хватит мечтать', 52000, 54000, [(52000, 52500), (52600, 53200), (53300, 54000)])]

    def test_grid_is_detected_only_for_whole_second_runs(self):
        self.assertEqual(vt.grid_spans(self.CONTEXT), [(30000, 40000)])
        natural = [{'text': 'a', 'start_ms': 30020, 'end_ms': 32000}, {'text': 'b', 'start_ms': 32000, 'end_ms': 34000},
                   {'text': 'c', 'start_ms': 34000, 'end_ms': 36000}]
        self.assertEqual(vt.grid_runs(natural), [])
        self.assertEqual(vt.grid_runs(self.CONTEXT[0]['phrases'][:2]), [])
        groups = vt.grid_groups(self.rows(), vt.grid_segments(self.CONTEXT))
        self.assertEqual(groups, [([0, 1, 2, 3, 4], (30000, 40000))])
        self.assertEqual(vt.grid_window(self.rows(), groups[0][0], groups[0][1], 60000), (25600, 48000))
        manual = self.rows()
        manual[2]['timing_quality'] = 'manual_phrase'
        self.assertEqual([g for g, _ in vt.grid_groups(manual, vt.grid_segments(self.CONTEXT))], [[0, 1], [3, 4]])
        # A long line from another recognizer is not confined to a grid crop.
        long = self.rows()
        long[1].update(text='Прямо сейчас пусть будут эти сто балерин', start_ms=31000, end_ms=48000)
        self.assertEqual([g for g, _ in vt.grid_groups(long, vt.grid_segments(self.CONTEXT))], [[2, 3, 4]])

    def test_window_leaves_out_the_held_word_of_the_previous_line(self):
        rows = [phrase('c', 'Как сто балерин', 21600, 25860, [(21600, 22000), (22100, 25300), (25300, 25860)])]
        rows += self.rows()
        vocal = {'intervals': [[21560, 27380]] + self.ACTIVITY['intervals']}
        groups = vt.grid_groups(rows, vt.grid_segments(self.CONTEXT))
        self.assertEqual(vt.grid_window(rows, groups[0][0], groups[0][1], 60000, vocal)[0], 27380)

    def test_passage_is_aligned_again_as_one_text(self):
        def align(text, a, b, source):
            self.assertEqual((a, b), (25600, 48000))
            self.assertEqual(text, ' '.join(self.TEXTS))
            return words(text, self.SUNG)
        hyp = {'phrases': self.rows()}
        changed = self.worker.realign_grid_runs('x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None,
                                                align=align)
        self.assertEqual(changed, 1)
        rows = {p['phrase_id']: p for p in hyp['phrases']}
        self.assertEqual([p['text'] for p in hyp['phrases']], self.TEXTS + ['Ну хватит мечтать'])
        self.assertEqual((rows['p']['start_ms'], rows['p']['end_ms']), (36560, 38100))
        self.assertEqual(rows['s']['start_ms'], 40200)
        self.assertEqual(rows['g']['start_ms'], 43960)
        self.assertEqual(rows['p']['grid_source_ms'], [32960, 33920])
        self.assertEqual(rows['n']['start_ms'], 52000)
        self.assertNotIn('grid_realigned', rows['n'])
        starts = [p['start_ms'] for p in hyp['phrases']]
        self.assertEqual(starts, sorted(starts))

    def test_partial_line_starts_with_its_voiced_stretch(self):
        sung = [t for i, t in enumerate(self.SUNG) if i not in (1, 2)]  # «повторит?», «Прямо» unmeasured
        def align(text, a, b, source):
            import re
            tokens = list(re.finditer(r'\S+', text))
            keep = [i for i in range(len(tokens)) if i not in (1, 2)]
            return [dict(text=tokens[i].group(), char_start=tokens[i].start(), char_end=tokens[i].end(),
                         start_ms=t[0], end_ms=t[1], method='qwen3_forced_aligner') for i, t in zip(keep, sung)]
        hyp = {'phrases': self.rows()}
        self.worker.realign_grid_runs('x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None, align=align)
        rows = {p['phrase_id']: p for p in hyp['phrases']}
        self.assertEqual(rows['p']['timing_quality'], 'word_partial')
        self.assertEqual(rows['p']['start_ms'], 36540)
        self.assertLessEqual(rows['k']['end_ms'], rows['p']['start_ms'])

    def test_unmeasured_line_takes_the_singing_before_the_next_line(self):
        # «Кто повторит?» and «Прямо» unmeasured (the text is a mishearing of
        # «И если не лень, то повторим»): the line keeps the singing up to the
        # voiced stretch where «Прямо сейчас» begins.
        def align(text, a, b, source):
            import re
            tokens = list(re.finditer(r'\S+', text))
            keep = list(range(3, len(tokens)))
            return [dict(text=tokens[i].group(), char_start=tokens[i].start(), char_end=tokens[i].end(),
                         start_ms=self.SUNG[i][0], end_ms=self.SUNG[i][1], method='qwen3_forced_aligner')
                    for i in keep]
        hyp = {'phrases': self.rows()}
        self.worker.realign_grid_runs('x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None, align=align)
        rows = {p['phrase_id']: p for p in hyp['phrases']}
        self.assertEqual(rows['p']['start_ms'], 36540)
        self.assertEqual(rows['k']['timing_quality'], 'phrase')
        self.assertGreaterEqual(rows['k']['start_ms'], 28900)
        self.assertLessEqual(rows['k']['end_ms'], 36540)
        self.assertGreaterEqual(rows['k']['end_ms'], 34880)

    def test_words_glued_to_a_voiced_crop_edge_are_not_measurements(self):
        vocal = {'intervals': [[21560, 27380], [28960, 31080]]}
        glued = words('сейчас Пусть будут', [(25860, 25900), (29000, 29400), (29500, 29900)])
        self.assertEqual([w['text'] for w in vt.grid_edge_words(glued, (25860, 40000), vocal)], ['Пусть', 'будут'])
        self.assertEqual(len(vt.grid_edge_words(glued, (25000, 40000), vocal)), 3)

    def test_misplaced_alignment_keeps_old_times(self):
        def align(text, a, b, source):
            return words(text, [(26000 + i * 120, 26100 + i * 120) for i in range(len(text.split()))])
        hyp = {'phrases': self.rows()}
        changed = self.worker.realign_grid_runs('x.wav', hyp, self.ACTIVITY, self.CONTEXT, lambda s: None,
                                                align=align)
        self.assertEqual(changed, 0)
        self.assertEqual(hyp['phrases'], self.rows())
        self.assertEqual(hyp['grid_realignment']['runs'][0]['reason'], 'alignment_outside_voice')


class HeldEndAndPlaceholderTests(unittest.TestCase):
    """«Давай, наливай»: «Как сто балерин» stopped after «сто» and «балерин»
    showed up as a lone chord «G»; singing nobody recognized had no line to
    type the words into."""

    VOCAL = {'intervals': [[21560, 27380], [28960, 31080], [40000, 46000]],
             'weak': [[21560, 27380], [28960, 31080], [40000, 46000]]}

    def test_line_ends_with_the_voice_of_its_unmeasured_last_word(self):
        row = phrase('c', 'Будущий день Как сто балерин', 21600, 25860, None)
        fitted, notes = vt.fit_phrase(row, self.VOCAL, None, 28900)
        self.assertEqual(fitted['end_ms'], 27530)
        self.assertTrue(any(n['reason'] == 'held_last_word' for n in notes))
        measured = phrase('m', 'Как сто балерин', 23000, 25860, [(23000, 23500), (23600, 24400), (24500, 25860)])
        self.assertEqual(vt.fit_phrase(measured, self.VOCAL, None, 28900)[0]['end_ms'], 25860)
        self.assertEqual(vt.fit_phrase(row, self.VOCAL, None, 26000)[0]['end_ms'], 26000)

    def test_unrecognized_singing_gets_an_editable_line(self):
        rows = [phrase('a', 'Первая строка песни', 28960, 31080, None),
                phrase('b', 'Последняя строка', 47000, 49000, None)]
        vocal = dict(self.VOCAL, lexical=[[40000, 46000]])
        out, count = vt.insert_placeholders(rows, vocal, 60000)
        self.assertEqual(count, 1)
        self.assertEqual([p['text'] for p in out], ['Первая строка песни', '???', 'Последняя строка'])
        self.assertEqual((out[1]['start_ms'], out[1]['end_ms']), (40000, 46000))
        again, more = vt.insert_placeholders(out, vocal, 60000)
        self.assertEqual(more, 0)
        lines = build_guitar_sheet(out, [dict(name='Am', start_ms=0, end_ms=60000)], 60000, vocal)
        from songs.sheet_lines import readable_sheet
        sheet = [l['text'] for l in readable_sheet(lines, events=[dict(name='Am', start_ms=0, end_ms=60000)]) if l.get('text')]
        self.assertIn('???', sheet)
        self.assertFalse(any(l.get('vocal') for l in lines))

if __name__ == '__main__':
    unittest.main()
