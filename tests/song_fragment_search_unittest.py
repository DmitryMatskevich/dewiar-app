"""Lyric-first retrieval regression checks; no network, ASR, or live settings."""
from pathlib import Path
import copy
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from songs_fragment_search import anchors, query_plan, publication_match, lyrics_only
from songs_text_postpass import tokens


def phrase(text, disputed=()):
    return dict(phrase_id='test',text=text,start_ms=1000,end_ms=10000,
                agreement=[dict(char_start=t['start'],char_end=t['end'],text=t['text'],
                    agree=1 if t['norm'] in disputed else 2,
                    needs_review=t['norm'] in disputed) for t in tokens(text)])


class FragmentSearchTests(unittest.TestCase):
    def test_uncertain_word_is_not_included_or_joined_across(self):
        p=phrase('Тихим вечером мы возвращались загадконо домой после долгой прогулки',{'загадконо'})
        before=copy.deepcopy(p)
        found=anchors([p])
        self.assertTrue(found)
        for a in found:
            self.assertNotIn('загадконо',a['text'])
            self.assertIn(a['text'],p['text'])
        self.assertEqual(before,p)

    def test_stale_votes_after_an_edit_are_rejected(self):
        p=phrase('Длинная дорога ведёт через старый город')
        p['text']='Совсем другие слова появились после ручной правки'
        self.assertEqual(anchors([p]),[])

    def test_verified_phrase_supports_unknown_proper_names(self):
        p=phrase('Через Лихоборы вечерним поездом вернулись мы домой')
        p.update(agreement=[],review_state='verified')
        self.assertEqual(anchors([p])[0]['evidence'],'human_verified')

    def test_metadata_cannot_replace_the_first_text_query(self):
        a=anchors([phrase('Вечерний поезд медленно уходит за деревню')])
        q=query_plan(a,'Неверное название','Неизвестный исполнитель')
        self.assertEqual(q[0]['stage'],'exact_fragment')
        self.assertNotIn('Неизвестный',q[0]['query'])
        self.assertNotIn('Неверное',q[0]['query'])
        self.assertTrue(q[0]['query'].startswith('"'))
        self.assertEqual(q[-1]['stage'],'metadata_fallback')
        self.assertLessEqual(len(q),8)

    def test_missing_evidence_is_not_reported_as_a_reliable_fragment(self):
        self.assertEqual(anchors([dict(text='Вечерний поезд медленно уходит за деревню')]),[])
        self.assertEqual(query_plan([],title='Название')[0]['stage'],'no_reliable_fragment')
        self.assertEqual(query_plan([]),[])
        self.assertEqual(anchors([phrase('Вечерний поезд медленно уходит за деревню')],limit=0),[])

    def test_vocalization_and_repeated_words_do_not_make_a_query(self):
        self.assertEqual(anchors([phrase('Ла-ла ла-ла ла-ла ла-ла')]),[])
        self.assertEqual(anchors([phrase('любовь любовь любовь любовь любовь')]),[])

    def test_chord_lines_and_section_labels_do_not_break_a_match(self):
        a=[dict(text='тихий вечер согревает окна нашего дома')]
        text='[Куплет 1]:\nDm\nтихий вечер согревает\nG7 C/E\nокна нашего дома'
        m=publication_match(text,a)
        self.assertEqual(m['status'],'single_fragment')
        self.assertTrue(m['hits'][0]['exact_full_anchor'])
        self.assertNotIn('Dm',lyrics_only(text))
        self.assertIn('В тексте Am звучит тихо',lyrics_only('В тексте Am звучит тихо'))

    def test_two_fragments_and_one_wrong_word_keep_a_partial_match_auditable(self):
        selected=[dict(text='тихий вечер согревает окна нашего дома'),
                  dict(text='утренний поезд медленно уходит далеко за деревню')]
        m=publication_match('тихий вечер согревает окна нашего дома. Утренний поезд медленно уходит далеко в поля.',selected)
        self.assertEqual(m['status'],'multiple_fragments')
        self.assertFalse(m['hits'][1]['exact_full_anchor'])
        self.assertFalse(m['version_verified'])

    def test_same_refrain_twice_is_not_two_distinct_anchors(self):
        line='тихий вечер согревает окна нашего дома'
        m=publication_match(line+'\n'+line,[dict(text=line),dict(text=line)])
        self.assertEqual(m['status'],'single_fragment')

    def test_200_ok_page_with_unrelated_body_has_no_lyric_match(self):
        m=publication_match('Бабушка — вход на сайт. Введите пароль. Реклама.',
                            [dict(text='Не довязала одну рукавицу Бабушка просто сегодня ушла')])
        self.assertEqual(m['status'],'no_exact_fragment')


if __name__=='__main__':unittest.main()
