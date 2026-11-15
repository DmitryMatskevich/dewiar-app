"""Words without a measured time stand where the voice is («Лишь тебя со мною нет»).

The phrases were whole 25-second recognizer chunks; the aligner measured
some words and left others («Ты, ты», «Два не отвеченных») without a time.
Spread evenly over the chunk, they — and the chords with them — covered the
intro and the break: intro chords stood over the first line and the chord
changes went out of time order.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs.chord_layout import build_guitar_sheet
from songs.sheet_lines import readable_sheet
from songs.word_timeline import timelines


def phrase(pid, text, start, end, times):
    """``times``: {word index: (start, end)} for the measured words."""
    tokens = list(re.finditer(r'\S+', text))
    words = [dict(text=tokens[i].group(), char_start=tokens[i].start(), char_end=tokens[i].end(),
                  start_ms=a, end_ms=b, method='qwen3_forced_aligner') for i, (a, b) in sorted(times.items())]
    return dict(phrase_id=pid, text=text, start_ms=start, end_ms=end, words=words, timing_quality='word_partial')


VOCAL = {'intervals': [[6380, 9580], [14820, 15360], [15840, 38240], [41120, 42360], [47880, 60000]]}
FIRST = 'Ты, ты живешь так далеко и так редко. Лишь тебя со мною нет. Два не отвеченных.'
SECOND = 'Звонка у тебя на телефоне'
CHORDS = [('Am', 6272), ('F', 7851), ('C', 8576), ('E7', 10709), ('Am', 12075), ('C', 14720), ('E7', 16213),
          ('Am', 17451), ('F', 35000), ('C', 36736), ('E7', 38443), ('Am', 39637), ('Cmaj7', 47915),
          ('E7', 49408), ('Am', 50859)]


def song():
    return [phrase('a', FIRST, 6320, 50000, {2: (16000, 16480), 3: (16560, 16880), 4: (16880, 17360),
                                               5: (18560, 18720), 6: (18720, 18880), 7: (18960, 19280),
                                               8: (35000, 35300), 9: (35400, 35800), 10: (35900, 36100),
                                               11: (36200, 36300), 12: (36300, 36900)}),
            phrase('b', SECOND, 50000, 60000, {1: (51200, 52000), 2: (52000, 52400), 3: (52560, 52800),
                                                 4: (52800, 53400)})]


def events():
    rows = [dict(name=n, start_ms=t) for n, t in CHORDS]
    for a, b in zip(rows, rows[1:]):
        a['end_ms'] = b['start_ms']
    rows[-1]['end_ms'] = 60000
    return rows


class WordTimelineTests(unittest.TestCase):
    def sheet(self):
        return build_guitar_sheet(song(), events(), 60000, VOCAL)

    def test_unmeasured_words_stand_in_the_voice_next_to_their_line(self):
        rows = song()
        cues = timelines(rows, VOCAL, 60000)
        first, second = cues[id(rows[0])], cues[id(rows[1])]
        # «Ты, ты»: in the voice right before «живешь», not at the chunk's start.
        self.assertGreaterEqual(first[0]['start_ms'], 14800)
        self.assertLessEqual(first[1]['end_ms'], 16000)
        # «Два не отвеченных. Звонка» starts a sentence: before «у тебя», after the break.
        self.assertGreaterEqual(first[13]['start_ms'], 47880)
        self.assertLessEqual(second[0]['end_ms'], 51200)
        # Measured words are untouched; every word is in time order.
        self.assertEqual((first[2]['start_ms'], first[2]['end_ms'], first[2]['measured']), (16000, 16480, True))
        flat = [c for c in first + second]
        self.assertTrue(all(a['start_ms'] <= b['start_ms'] for a, b in zip(flat, flat[1:])))

    def test_intro_and_break_chords_stay_out_of_the_text(self):
        sheet = self.sheet()
        texts = [r for r in sheet if r.get('text')]
        self.assertGreaterEqual(texts[0]['start_ms'], 14800)
        intro = sheet[0]
        self.assertFalse(intro.get('text'))
        self.assertEqual([c['name'] for c in intro['unplaced_chords']][:5], ['Am', 'F', 'C', 'E7', 'Am'])
        on_text = [c['start_ms'] for r in texts for c in r['chords'] if not c.get('continued')]
        self.assertFalse([t for t in on_text if t < 14000 or 38400 < t < 47800], on_text)
        gap = next(r for r in sheet if not r.get('text') and r['start_ms'] >= 36000 and r['end_ms'] <= 48000
                   and not r.get('pause'))
        self.assertEqual([c['name'] for c in gap['unplaced_chords'] if c['start_ms'] >= 38000], ['E7', 'Am'])

    def test_chords_follow_the_text_in_time_order(self):
        order = []
        for row in self.sheet():
            marks = row['chords'] if row.get('text') else row['unplaced_chords']
            order.extend(c['start_ms'] for c in sorted(marks, key=lambda c: c.get('char_index') or 0)
                         if not c.get('continued'))
        self.assertEqual(order, sorted(order))

    def test_squeezed_measurements_are_not_measurements(self):
        text = 'Мы не виделись сто лет. Есть пиво и гитара, лишь тебя со мною нет.'
        row = phrase('c', text, 60000, 72000, {0: (60600, 60900), 1: (61000, 61200), 2: (63100, 63900),
                                                3: (64100, 64500), 4: (64600, 65000), 5: (65600, 65620),
                                                6: (65620, 65650), 7: (65650, 65700), 8: (65700, 65760),
                                                9: (65760, 65790), 10: (65790, 65800), 11: (65800, 65900),
                                                12: (69400, 69500), 13: (69500, 70400)})
        cues = timelines([row], {'intervals': [[60000, 71500]]}, 72000)[id(row)]
        self.assertFalse(any(c['measured'] for c in cues[5:11]))
        self.assertGreater(cues[8]['start_ms'] - cues[5]['start_ms'], 600)   # spread, not an instant
        self.assertLessEqual(cues[11]['end_ms'], 69400)

    def test_highlight_uses_the_same_times(self):
        lines = [l for l in readable_sheet(self.sheet(), events=events()) if l.get('text')]
        first = lines[0]
        self.assertTrue(first['text'].startswith('Ты, ты живешь'))
        self.assertGreaterEqual(first['display_words'][0]['start_ms'], 14800)
        self.assertEqual(first['display_words'][0]['quality'], 'estimated_phrase')
        self.assertEqual(first['display_words'][2]['quality'], 'aligned')

    def test_without_a_vocal_map_nothing_changes(self):
        self.assertEqual(timelines(song(), None, 60000), {})
        sheet = build_guitar_sheet(song(), events(), 60000)
        self.assertFalse(any('cues' in r for r in sheet))


if __name__ == '__main__':
    unittest.main()
