"""Blank lines and labels of the chord sheet from the confirmed publication («Я счастливый», Лепс).

Stanzas guessed «by four, at the longest pause» agreed with the publication
in a quarter of the cases: the first sung line was cut at «?» and every
quatrain shifted by a line. The publication's own lines and quatrains are
found in the sung words; without one, a blank line marks only a break or a
long pause. The user's layout (blank lines, labels, line breaks) wins.
"""
import copy
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs.chord_layout import build_guitar_sheet
from songs.layout_edit import edit_layout, resolve_layout
from songs.reference_fragments import lyrics_only, lyrics_with_labels
from songs.sheet_lines import readable_sheet
from songs.source_layout import word_layout
from songs.store import SongStore

PAGE = '''Вступление: Gm Cm7
Кто поверит? Я и сам не верю
То ль на счастье, то ли на беду
У меня семь пятниц на неделе
И тринадцать месяцев в году

Моё небо синее в алмазах
Что-то мне по жизни принесёт
Я крещён, а может быть помазан
В общем, я счастливый, вот и всё!

Припев:
Я счастливый, как никто!
Я счастливый лет уж сто!
Я счастливый – я не вру!
Так счастливым и уйду!'''

SUNG = [  # recognized phrases: cut by breaths, not by the poem
    ('a', 'Кто поверит?'), ('b', 'Я и сам не верю То ль на счастье, то ли на беду.'),
    ('c', 'У меня семь пятниц на неделе, И тринадцать месяцев в году.'),
    ('d', 'Моё небо синее в алмазах, Что-то мне по жизни принесёт.'),
    ('e', 'Я крещён, а может быть помазан. В общем, я счастливый, вот и всё!'),
    ('f', 'Я счастливый, как никто! Я счастливый лет уж сто!'),
    ('g', 'Я счастливый – я не вру! Так счастливым и уйду!'),
    ('h', 'Я счастливый, как никто! Я счастливый лет уж сто!'),   # the chorus again
    ('i', 'Я счастливый – я не вру! Так счастливым и уйду!'),
]


def phrases(pause_after=()):
    out, t = [], 27500
    for pid, text in SUNG:
        words, start = [], t
        for m in re.finditer(r'\S+', text):
            words.append(dict(text=m.group(), char_start=m.start(), char_end=m.end(), start_ms=t, end_ms=t + 300,
                              method='qwen3_forced_aligner'))
            t += 330
        out.append(dict(phrase_id=pid, text=text, start_ms=start, end_ms=t - 30, words=words,
                        timing_quality='word_estimated', alignment_stale=False))
        t += 3500 if pid in pause_after else 400
    return out


def reference():
    text, labels = lyrics_with_labels(PAGE)
    return dict(url='https://amdm.ru/x', plain_lyrics=text, labels=labels, text_confirmed=True)


def sheet(rows, refs=(), manual=None):
    built = build_guitar_sheet(rows, [dict(name='Gm', start_ms=27000, end_ms=200000)], 200000)
    layout, _ = word_layout(built, list(refs))
    return [l for l in readable_sheet(built, layout=layout, manual=manual, events=[]) if l.get('text')]


def stanzas(lines):
    return [l['text'] for l in lines if l.get('stanza_start')]


class SourceLayoutTests(unittest.TestCase):
    def test_labels_are_kept_apart_from_the_sung_text(self):
        text, labels = lyrics_with_labels(PAGE)
        self.assertEqual(text, lyrics_only(PAGE))
        self.assertEqual([l['text'] for l in labels], ['Вступление', 'Припев'])
        self.assertEqual(text.splitlines()[labels[1]['line']], 'Я счастливый, как никто!')

    def test_lines_and_quatrains_follow_the_publication(self):
        lines = sheet(phrases(), [reference()])
        texts = [l['text'] for l in lines]
        self.assertIn('Кто поверит? Я и сам не верю', texts)          # one printed line, not cut at «?»
        self.assertIn('То ль на счастье, то ли на беду.', texts)
        self.assertEqual(stanzas(lines), ['Кто поверит? Я и сам не верю', 'Моё небо синее в алмазах,',
                                          'Я счастливый, как никто!', 'Я счастливый, как никто!'])
        # The chorus is labelled where it is sung, the repeat included.
        self.assertEqual([l['text'] for l in lines if l.get('source_label') == 'Припев'],
                         ['Я счастливый, как никто!', 'Я счастливый, как никто!'])

    def test_without_a_publication_only_long_pauses_part_the_text(self):
        self.assertEqual(stanzas(sheet(phrases())), ['Кто поверит?'])
        lines = sheet(phrases(pause_after={'e'}))
        self.assertEqual(len(stanzas(lines)), 2)
        self.assertTrue(stanzas(lines)[1].startswith('Я счастливый, как никто!'))

    def test_another_song_is_not_used(self):
        other = dict(plain_lyrics='Совсем другая песня\nО другом и не так\n\nИ припев у неё другой', text_confirmed=True)
        self.assertEqual(word_layout(build_guitar_sheet(phrases(), [], 200000), [other])[0], {})

    def test_the_users_layout_wins(self):
        rows = phrases()
        first = sheet(rows, [reference()])
        moe = next(l for l in first if l['text'].startswith('Моё небо'))
        sam = next(l for l in first if l['text'].startswith('Кто поверит'))
        manual = {'breaks': {moe['layout_key']: False}, 'notes': {moe['layout_key']: '×2'},
                  'cuts': {'b:0': True}}
        lines = sheet(rows, [reference()], manual)
        self.assertNotIn('Моё небо синее в алмазах,', stanzas(lines))
        self.assertEqual(next(l for l in lines if l['text'].startswith('Моё небо'))['note'], '×2')
        self.assertIn('Кто поверит?', [l['text'] for l in lines])        # the user cut the printed line
        self.assertEqual(sam['layout_key'], 'a:0')

    def test_edit_layout_is_stored_and_follows_its_word(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = SongStore(Path(tmp.name)); song = store.create('ТЕСТ')
        row = store.patch(song['song_id'], {'phrases': phrases()})
        at = row['phrases'][3]['text'].index('Что-то')
        args = dict(song_id=row['song_id'], key=f'd:{at}', action='break', value=True)
        row = edit_layout(store, dict(args, base_revision=row['revision']))
        self.assertEqual(row['sheet_layout'], {'breaks': {f'd:{at}': True}, 'anchors': {f'd:{at}': 'Что-то'}})
        row = edit_layout(store, dict(args, key=f'd:{at + 2}', action='note', value='  Припев ', base_revision=row['revision']))
        self.assertEqual(row['sheet_layout']['notes'], {f'd:{at}': 'Припев'})   # a letter of the word names it
        with self.assertRaises(ValueError):
            edit_layout(store, dict(args, base_revision=1))
        # The phrase was edited before the word: the place moves with it.
        edited = copy.deepcopy(row['phrases'])
        edited[3]['text'] = 'Моё небо синее, синее в алмазах, Что-то мне по жизни принесёт.'
        moved = edited[3]['text'].index('Что-то')
        self.assertEqual(resolve_layout(row['sheet_layout'], edited)['breaks'], {f'd:{moved}': True})
        edited[3]['text'] = 'Моё небо синее в алмазах.'
        self.assertEqual(resolve_layout(row['sheet_layout'], edited)['breaks'], {})
        row = edit_layout(store, dict(args, value=None, base_revision=row['revision']))
        row = edit_layout(store, dict(args, action='note', value=None, base_revision=row['revision']))
        self.assertEqual(row['sheet_layout'], {})


if __name__ == '__main__':
    unittest.main()
