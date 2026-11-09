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


class VoicedPauseRealignTests(unittest.TestCase):
    """«Давай, наливай»: «Но ты не одна, и ты не один» packed into 1.8 s;
    «и ты не один» was sung in the «pause» after it and its chords (Am, D, G)
    stood on a line without text."""

    VOCAL = {'intervals': [[141340, 143800], [144920, 147100], [148560, 154860]],
             'weak': [[141340, 143800], [144920, 147100], [148560, 154860]]}
    A = 'Но ты не одна, и ты не один,'
    B = 'Ведь нас окружают всё те же незримые Сто балерин.'
    SUNG = [(141400, 141800), (141900, 142200), (142300, 142500), (142600, 143700), (144950, 145200),
            (145300, 145600), (145700, 145900), (146000, 147000),
            (148600, 148800), (149000, 149400), (149500, 150000), (150050, 150250), (150900, 151100),
            (151300, 151500), (151500, 152400), (152600, 153300), (153400, 154000)]

    def setUp(self):
        import types
        fake = types.ModuleType('soundfile')
        fake.info = lambda path: types.SimpleNamespace(duration=200.0)
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
        return [phrase('a', self.A, 140400, 142160, [(140400, 141040), None, None, None, None, (141680, 141920), None,
                                                     (141920, 142160)] and None),
                phrase('b', self.B, 148600, 153960)]

    def test_group_only_for_a_short_voiced_pause_without_other_words(self):
        rows = self.rows()
        self.assertEqual(vt.voiced_gap_groups(rows, self.VOCAL, 200000), [([0, 1], (140400, 153960))])
        lexical = dict(self.VOCAL, lexical=[[144900, 147200]])
        lexical['intervals'] = [[141340, 143800], [144920, 148400], [148560, 154860]]
        self.assertEqual(vt.voiced_gap_groups(rows, dict(lexical, weak=lexical['intervals']), 200000), [])
        far = [rows[0], dict(rows[1], start_ms=160000, end_ms=165000)]
        self.assertEqual(vt.voiced_gap_groups(far, self.VOCAL, 200000), [])

    def test_line_takes_its_singing_back_from_the_pause(self):
        def align(text, a, b, source):
            self.assertEqual(text, self.A + ' ' + self.B)
            return words(text, self.SUNG)
        hyp = {'phrases': self.rows()}
        changed = self.worker.realign_voiced_gaps('x.wav', hyp, self.VOCAL, [], lambda s: None, align=align)
        self.assertEqual(changed, 1)
        a = hyp['phrases'][0]
        self.assertEqual((a['start_ms'], a['end_ms']), (141400, 147000))
        self.assertLessEqual(vt.uncovered_voice(hyp['phrases'], self.VOCAL, 140000, 148000), 200)

    def test_no_change_when_the_alignment_leaves_the_pause_empty(self):
        squeezed = [(141400 + i * 100, 141480 + i * 100) for i in range(8)] + self.SUNG[8:]
        hyp = {'phrases': self.rows()}
        changed = self.worker.realign_voiced_gaps('x.wav', hyp, self.VOCAL, [], lambda s: None,
                                                  align=lambda text, a, b, source: words(text, squeezed))
        self.assertEqual(changed, 0)
        self.assertEqual(hyp['phrases'], self.rows())
        self.assertEqual(hyp['gap_realignment']['runs'][0]['reason'], 'no_better_than_before')

    def test_no_change_when_a_word_is_stretched_over_silence(self):
        # Covers the singing, but «один,» also runs over the silent bar
        # before the next line: not better.
        stretched = self.SUNG[:7] + [(146000, 148500)] + self.SUNG[8:]
        hyp = {'phrases': self.rows()}
        changed = self.worker.realign_voiced_gaps('x.wav', hyp, self.VOCAL, [], lambda s: None,
                                                  align=lambda text, a, b, source: words(text, stretched))
        self.assertEqual(changed, 0)
        self.assertEqual(hyp['phrases'], self.rows())

    def test_held_last_vowel_is_not_a_voiced_pause(self):
        # «Давай, наливай, поговорим.»: «наливай-ай» held 1 s after its
        # measured end, then a silent bar. The sheet keeps the held vowel
        # with its line; nothing to realign.
        vocal = {'intervals': [[163940, 165900], [167900, 169820], [171580, 177120]]}
        vocal['weak'] = vocal['intervals']
        rows = [phrase('a', 'Давай, наливай, поговорим.', 163960, 168920,
                       [(163960, 164440), (164600, 164920), (167880, 168920)]),
                phrase('b', 'Будущий день покажется светлым,', 171720, 175000,
                       [(171720, 172600), (172680, 172840), (173320, 174040), (174120, 175000)])]
        self.assertLessEqual(vt.uncovered_voice(rows, vocal, 163000, 172000), 200)  # onsets only
        self.assertEqual(vt.voiced_gap_groups(rows, vocal, 200000), [])


    def test_word_placed_after_its_voice_inside_one_line(self):
        # «Давай, наливай, поговорим Будущий день покажется светлым,»:
        # «поговорим» placed at 89.2 s, sung at 85.4–87.2; the sheet cut the
        # line there and showed Am D G alone.
        vocal = {'intervals': [[81440, 83400], [85400, 87160], [89080, 94680]]}
        vocal['weak'] = vocal['intervals']
        text = 'Давай, наливай, поговорим Будущий день покажется светлым,'
        late = [(81440, 81920), (82080, 82320), (89200, 89440), (89440, 90080), (90160, 90400),
                (90880, 91600), (91600, 92480)]
        sung = [(81440, 81920), (82080, 82720), (85400, 87100), (89100, 90080), (90160, 90400),
                (90880, 91600), (91600, 92480)]
        rows = [phrase('a', 'Ну хватит мечтать, пора продолжать.', 73920, 78560,
                       [(73920, 74080), (74160, 74560), (74560, 75040), (75040, 75200), (75200, 78560)]),
                phrase('b', text, 81440, 92480, late)]
        vocal_all = dict(vocal, intervals=[[73840, 78600], *vocal['intervals']])
        vocal_all['weak'] = vocal_all['intervals']
        self.assertEqual(vt.voiced_gap_groups(rows, vocal_all, 200000), [([1], (81440, 92480))])
        hyp = {'phrases': rows}
        changed = self.worker.realign_voiced_gaps('x.wav', hyp, vocal_all, [], lambda s: None,
                                                  align=lambda t, a, b, source: words(t, sung))
        self.assertEqual(changed, 1)
        got = hyp['phrases'][1]['words'][2]
        self.assertEqual((got['text'], got['start_ms']), ('поговорим', 85400))


    def test_best_fit_to_the_voice_wins_over_more_words(self):
        # Mixture: all 7 words, but «наливай» where «поговорим» was sung.
        # Vocals: 6 words in the right places. The vocals result is taken.
        vocal = {'intervals': [[73840, 78600], [81440, 83400], [85400, 87160], [89080, 94680]]}
        vocal['weak'] = vocal['intervals']
        text = 'Давай, наливай, поговорим Будущий день покажется светлым,'
        late = [(81440, 81920), (82080, 82320), (89200, 89440), (89440, 90080), (90160, 90400),
                (90880, 91600), (91600, 92480)]
        rows = [phrase('a', 'Ну хватит мечтать, пора продолжать.', 73920, 78560,
                       [(73920, 74080), (74160, 74560), (74560, 75040), (75040, 75200), (75200, 78560)]),
                phrase('b', text, 81440, 92480, late)]
        wrong = [(81460, 81940), (86020, 86340), (89220, 89460), (89460, 90020), (90180, 90420),
                 (90820, 91620), (91620, 92420)]
        right = [(81460, 81940), (81940, 82340), (85380, 86900), (89220, 90820), None,
                 (90820, 91460), (91620, 92580)]

        def align(t, a, b, source):
            got = words(t, [x or (0, 0) for x in (wrong if source == 'mixture' else right)])
            return [w for w, x in zip(got, wrong if source == 'mixture' else right) if x]
        hyp = {'phrases': rows}
        changed = self.worker.realign_voiced_gaps('x.wav', hyp, vocal, [], lambda s: None, align=align,
                                                  vocals='v.wav')
        self.assertEqual(changed, 1)
        run = hyp['gap_realignment']['runs'][0]
        self.assertEqual(run['source'], 'vocals')
        got = {w['text']: w['start_ms'] for w in hyp['phrases'][1]['words']}
        self.assertEqual(got['поговорим'], 85380)


class HeldEndRerunTests(unittest.TestCase):
    def test_rerun_does_not_hold_the_line_again(self):
        # The stem sings without a break (one interval for the whole song):
        # every rerun used to add another 3 s to the line («Тишины хочу»).
        vocal = {'intervals': [[26280, 262940]]}
        text = 'всё плохое улетит в тучи неба.'
        row = phrase('a', text, 171980, 175000, [(171980, 172220), (172220, 172860), (172940, 173340),
                                                 None and (0, 0) or (173340, 173400), (173400, 174060),
                                                 (174060, 174620)])
        row['words'] = row['words'][:5]  # «неба.» not measured
        once, _ = vt.fit_phrases([row], vocal)
        twice, _ = vt.fit_phrases(once, vocal)
        self.assertEqual(once[0]['end_ms'], 178000)
        self.assertEqual(twice[0]['end_ms'], 178000)
        grown = dict(once[0], end_ms=184000)
        fixed, _ = vt.fit_phrases([grown], vocal)
        self.assertEqual(fixed[0]['end_ms'], 178000)


class HeldVowelSheetTests(unittest.TestCase):
    """«Ты так хороша-а»: the Am changed under the held «а» showed on a line
    of its own (no text) below the line that was still sung."""

    VOCAL = {'intervals': [[126140, 128640], [130100, 132500]]}
    ROWS = [phrase('a', 'Ты так хороша,', 126000, 127600, [(126000, 126400), (126500, 126900), (127120, 127600)]),
            phrase('b', 'мне не объяснить.', 130200, 131900, [(130200, 130500), (130560, 130720), (130720, 131900)])]
    EVENTS = [dict(name='Em', start_ms=125900, end_ms=128000), dict(name='Am', start_ms=128000, end_ms=130150),
              dict(name='D', start_ms=130150, end_ms=132000)]

    def test_chord_under_held_vowel_stays_on_its_line(self):
        sheet = build_guitar_sheet(self.ROWS, self.EVENTS, 133000, self.VOCAL)
        line = next(r for r in sheet if r.get('text') == 'Ты так хороша,')
        self.assertIn(('Am', 7), [(m['name'], m['char_index']) for m in line['chords']])
        self.assertEqual(line['end_ms'], 128640 + 150)
        gaps = [r for r in sheet if not r.get('text') and 127600 <= r['start_ms'] < 130200]
        self.assertFalse(any(g['unplaced_chords'] for g in gaps), gaps)
        # Measured words keep their times; only the line span grows.
        self.assertEqual(line['words'][-1]['end_ms'], 127600)

    def test_no_voice_after_the_word_no_change(self):
        vocal = {'intervals': [[126140, 127650], [130100, 132500]]}
        sheet = build_guitar_sheet(self.ROWS, self.EVENTS, 133000, vocal)
        line = next(r for r in sheet if r.get('text') == 'Ты так хороша,')
        self.assertLessEqual(line['end_ms'], 127800)
        self.assertNotIn('Am', [m['name'] for m in line['chords']])

if __name__ == '__main__':
    unittest.main()
