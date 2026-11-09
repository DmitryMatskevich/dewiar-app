"""Publications and pasted texts with chords («Не унывай» on mychords.net,
«Гоп со смыком» with footnote marks, «Давай, наливай» misheard line)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from songs.reference_fragments import lyrics_only
from songs.reference_text import reference_candidates, tokens
from songs.sources import _embedded_lyrics, _VisibleText

MYCHORDS = ('<html><body><div class="w-words__text" itemprop="text">'
            '<div class="single-line">Аккорды: <span class="b-accord__symbol">F</span> '
            '<span class="b-accord__symbol">Gadd9</span></div>'
            '<div class="single-line"><span class="b-accord__symbol">Gadd9</span> можно заменить на G</div>'
            '<div>&nbsp;</div><div class="single-line"><span class="b-lr-section">Проигрыш</span>: '
            '<span class="b-accord__symbol">F</span> <span class="b-accord__symbol">C</span> - 2 раза</div>'
            '<div class="single-line"><span class="b-lr-section">Куплет</span> 1:</div>'
            '<div class="single-line">Знаешь, а не сложно быть счастливым</div>'
            '<div class="single-line">Не унывать, не вешать нос и жить</div>'
            '<div>&nbsp;</div><div class="single-line"><span class="b-lr-section">Припев</span>:</div>'
            '<div class="single-line">И первый шаг, я с той ноги</div></div>'
            '<div class="comments">Комментарий</div></body></html>')


class SourceTextTests(unittest.TestCase):
    def test_mychords_page_gives_only_sung_lines(self):
        text = lyrics_only(_embedded_lyrics(MYCHORDS, 'https://mychords.net/ru/a/1-a.html'))
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        self.assertEqual(lines, ['Знаешь, а не сложно быть счастливым', 'Не унывать, не вешать нос и жить',
                                 'И первый шаг, я с той ноги'])

    def test_pasted_text_with_chords(self):
        pasted = ('Куплет 1:\nAm        G\nЗнаешь, а не сложно быть счастливым\n'
                  '[Am]Не унывать, не [G]вешать нос и жить\n| F | C | x2\nD7sus4  E7/G#  Bm7b5/A\n'
                  'Припев: И первый шаг, я с той ноги\nВ тексте Am звучит тихо')
        lines = [l.strip() for l in lyrics_only(pasted).splitlines() if l.strip()]
        self.assertEqual(lines, ['Знаешь, а не сложно быть счастливым', 'Не унывать, не вешать нос и жить',
                                 'И первый шаг, я с той ноги', 'В тексте Am звучит тихо'])

    def test_amdm_block_numbers_and_numbered_labels(self):
        page = ('<html><nav>Аккорды Новинки</nav><pre itemprop="chordsBlock" class="podbor__text">Припев\n'
                '<div class="podbor__chord" data-chord="G"><span>G</span></div>    <div class="podbor__chord"><span>Am</span></div>\n'
                'Будущий день покажется светлым Как 100 балерин.\n1 куплет\nА где мой покой, я словно в бреду\n'
                '</pre><div>Комментарии</div></html>')
        lines = [l.strip() for l in lyrics_only(_embedded_lyrics(page, 'https://amdm.ru/akkordi/a/1/b/')).splitlines() if l.strip()]
        self.assertEqual(lines, ['Будущий день покажется светлым Как сто балерин.', 'А где мой покой, я словно в бреду'])
        self.assertIn('в 1945 году', lyrics_only('в 1945 году'))

    def test_footnote_marks_are_not_words(self):
        parser = _VisibleText()
        parser.feed('<p>Жил на Лихоборах<sup>3</sup> Гоп со смыком, славился своим огромным приком<sup>4</sup></p>')
        text = ''.join(parser.parts)
        self.assertIn('Лихоборах Гоп', text)
        self.assertNotIn('приком4', text)

    def test_misheard_line_between_firm_anchors(self):
        phrases = [dict(phrase_id='a', text='Будущий день покажется светлым, как сто балерин', start_ms=0, end_ms=9000),
                   dict(phrase_id='b', text='Кто повторит?', start_ms=9000, end_ms=12000),
                   dict(phrase_id='c', text='Прямо сейчас пусть будут эти', start_ms=12000, end_ms=15000)]
        ref = 'Будущий день покажется светлым\nКак сто балерин\nИ если не лень, то повторим\nПрямо сейчас пусть будут эти'
        found = reference_candidates(phrases, [dict(plain_lyrics=ref, url='https://amdm.ru/x')])
        self.assertEqual([(c['current']['phrase_id'], c['current']['text'], c['alternative']['text']) for c in found],
                         [('b', 'Кто повторит', 'И если не лень, то повторим')])
        self.assertFalse(found[0]['auto_apply'])
        # Without firm anchors on both sides a dissimilar line is not replaced.
        loose = reference_candidates(phrases[1:], [dict(plain_lyrics='И если не лень, то повторим\nПрямо сейчас', url='x')])
        self.assertEqual(loose, [])


if __name__ == '__main__':
    unittest.main()
