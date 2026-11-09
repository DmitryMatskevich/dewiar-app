"""Verse lines and quatrains on the chord sheet («Ленинград-МГА»).

The recognizer cut lines where the singer breathed: «Я молча | пил, а лес
валил, Летели щепки.», «А срок | костил, я не просил». Every breath became a
pause row and every pause row ended a line. Lines now follow the verse and
are grouped into stanzas of four.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs.chord_layout import build_guitar_sheet
from songs.sheet_lines import readable_sheet, song_meter, stanza_starts, verse_cuts, syllables


def timed(pid, text, start, step=320, pause_after=()):
    words, t = [], start
    for k, m in enumerate(re.finditer(r'\S+', text)):
        words.append(dict(text=m.group(), char_start=m.start(), char_end=m.end(), start_ms=t, end_ms=t + step - 20,
                          method='qwen3_forced_aligner'))
        t += step + (600 if k in pause_after else 0)
    return dict(phrase_id=pid, text=text, start_ms=start, end_ms=t - 20, words=words,
                timing_quality='word_estimated', alignment_stale=False)


VERSE = [
    ('a', 'На зоне был, не фраерил, Там это ни к чему, Я молча'),
    ('b', 'пил, а лес валил, Летели щепки.'),
    ('c', 'Хозяин бил, я не скулил, И сил упадок не косил, А срок'),
    ('d', 'костил, я не просил, Я парень крепкий.'),
]


def phrases():
    out, t = [], 35000
    for pid, text in VERSE:
        row = timed(pid, text, t)
        out.append(row)
        t = row['end_ms'] + 60   # a breath inside the verse
    return out


class VerseLineTests(unittest.TestCase):
    def sheet(self, rows=None, starts=()):
        rows = rows or phrases()
        vocal = {'intervals': [[34000, 60000]], 'weak': [[34000, 60000]]}
        return readable_sheet(build_guitar_sheet(rows, [dict(name='Am', start_ms=34000, end_ms=60000)], 61000, vocal),
                              section_starts=starts, events=[dict(name='Am', start_ms=34000, end_ms=60000)])

    def test_a_breath_does_not_cut_the_verse(self):
        lines = [l['text'] for l in self.sheet() if l.get('text')]
        self.assertIn('Я молча пил, а лес валил,', lines)
        self.assertIn('А срок костил, я не просил,', lines)
        self.assertNotIn('Я молча', lines)
        self.assertEqual(' '.join(lines), ' '.join(t for _, t in VERSE))

    def test_two_quatrains(self):
        lines = [l for l in self.sheet() if l.get('text')]
        self.assertEqual(len(lines), 8)
        self.assertEqual([k for k, l in enumerate(lines) if l.get('stanza_start')], [0, 4])

    def test_words_keep_their_phrase_and_timing(self):
        lines = [l for l in self.sheet() if l.get('text')]
        line = next(l for l in lines if l['text'].startswith('Я молча'))
        self.assertEqual(line['source_phrase_ids'], ['a', 'b'])
        self.assertEqual([w['phrase_id'] for w in line['display_words']], ['a', 'a', 'b', 'b', 'b', 'b'])
        self.assertTrue(all(l['chords'] and l['chords'][0]['char_index'] == 0 for l in lines))

    def test_section_start_opens_a_stanza_only_at_its_first_slice(self):
        lines = [l for l in self.sheet(starts=['c']) if l.get('text')]
        self.assertEqual([k for k, l in enumerate(lines) if l.get('stanza_start')], [0, 4])

    def test_meter_and_cuts(self):
        tokens = [dict(text=w, bonus=0.0) for w in 'раз два три четыре пять шесть семь восемь'.split()]
        tokens[3]['bonus'] = 3.0
        self.assertEqual(verse_cuts(tokens, 6), [3])
        self.assertEqual(song_meter([dict(text='а' * 8, clear_end=True)] * 5), 8)
        self.assertEqual(syllables('Летели щепки'), 5)

    def test_stanzas_follow_long_pauses(self):
        self.assertEqual(stanza_starts([0, 0, 0, 0, 0, 0, 0]), [4])
        self.assertEqual(stanza_starts([0, 2500, 0, 0, 0]), [2])


if __name__ == '__main__':
    unittest.main()
