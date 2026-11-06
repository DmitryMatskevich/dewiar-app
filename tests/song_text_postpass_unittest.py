"""Post-ASR candidate checks: no recordings, network, models, or working state."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from songs_text_postpass import boundary_candidates, lexical_signals, reference_candidates


def phrase(text, number=0):
    return dict(phrase_id=str(number), text=text, start_ms=number*10000,
                end_ms=(number+1)*10000, words=[], review_state='verified')


class PostpassTests(unittest.TestCase):
    def test_real_boundary_error_can_be_found_without_an_external_source(self):
        rows = [phrase('Сунул с дуру кабелина две ноги', 0),
                phrase('Сунул с дурука бели на две ноги', 1)]
        before = copy.deepcopy(rows)
        candidates = boundary_candidates(rows)['candidates']
        self.assertEqual(len(candidates), 1)
        one = candidates[0]
        self.assertEqual(one['current']['text'], 'дуру кабелина')
        self.assertEqual(one['alternative']['text'], 'дурука бели на')
        self.assertFalse(one['auto_apply'])
        self.assertEqual(one['status'], 'needs_target_audio_check')
        self.assertEqual(rows, before)

    def test_dictionary_unknown_is_not_error_and_user_lexicon_is_preserved(self):
        p = phrase('Лихоборах дурука бели на ла-ла-ла')
        known = lambda w: w in {'бели', 'на'}
        signals = lexical_signals([p], known, lexicon=['Лихоборах'])
        self.assertEqual([s['text'] for s in signals], ['дурука'])
        self.assertEqual(signals[0]['status'], 'dictionary_unknown_not_error')
        self.assertEqual(p['text'][signals[0]['char_start']:signals[0]['char_end']], 'дурука')

    def test_ambiguous_correct_words_do_not_get_automatically_merged(self):
        rows = [phrase('Мы пошли на встречу', 0), phrase('Мы пошли навстречу', 1)]
        candidates = boundary_candidates(rows)['candidates']
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]['auto_apply'])
        self.assertNotIn('probability', candidates[0])

    def test_bad_publication_spacing_is_only_an_alternative(self):
        rows = [phrase('Пусть проходит это время')]
        refs = [dict(plain_lyrics='Пустьпроходит это время', url='https://example.org/a')]
        item = boundary_candidates(rows, refs)['candidates'][0]
        self.assertEqual(item['current']['text'], 'Пусть проходит')
        self.assertEqual(item['alternative']['text'], 'Пустьпроходит')
        self.assertFalse(item['auto_apply'])
        self.assertFalse(item['alternative']['version_verified'])

    def test_copied_publications_do_not_create_independent_votes(self):
        rows = [phrase('Он любил в разнос гулять')]
        refs = [dict(plain_lyrics='Он любил вразнос гулять', url='https://example.org/'+str(i)) for i in range(4)]
        self.assertEqual(len(boundary_candidates(rows, refs)['candidates']), 1)
        self.assertEqual(len(reference_candidates(rows, refs)), 0)  # needs two right anchors

    def test_short_anchored_reference_candidate_carries_provenance_not_fake_word_time(self):
        rows = [phrase('Вчера он сунул с дурука бели на две ноги в штанину')]
        ref = dict(plain_lyrics='Вчера он сунул сдуру, кобелина две ноги в штанину', url='https://example.org/song')
        before = copy.deepcopy(rows)
        candidates = reference_candidates(rows, [ref])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['current']['text'], 'с дурука бели на')
        self.assertEqual(candidates[0]['alternative']['text'], 'сдуру, кобелина')
        self.assertEqual(candidates[0]['current']['start_ms'], 0)
        self.assertEqual(candidates[0]['current']['end_ms'], 10000)
        self.assertEqual(candidates[0]['alternative']['url'], ref['url'])
        self.assertFalse(candidates[0]['auto_apply'])
        self.assertEqual(rows, before)

    def test_different_verse_and_deleted_repeats_are_not_suggested_as_repairs(self):
        rows = [phrase('Вчера он выбрал звёзды над рекой и слушал прибой')]
        refs = [dict(plain_lyrics='Вчера он выбрал книги про осень и слушал прибой')]
        self.assertEqual(reference_candidates(rows, refs), [])
        self.assertEqual(reference_candidates([phrase('Свет погас. Свет погас.')],
                         [dict(plain_lyrics='Свет погас.')]), [])

    def test_large_input_has_explicit_limit_and_unchanged_source(self):
        rows = [phrase('ла '*6100)]
        self.assertEqual(boundary_candidates(rows)['status'], 'too_large')
        self.assertEqual(reference_candidates(rows, [dict(plain_lyrics='ла')]), [])

    def test_same_reference_repair_is_not_duplicated_by_case_or_spacing(self):
        rows = [phrase('Вот тогда он нагорел меня вчера')]
        refs = [dict(plain_lyrics='Вот тогда он нагрел меня вчера'),
                dict(plain_lyrics='Вот тогда он НАГРЕЛ  меня вчера')]
        self.assertEqual(len(reference_candidates(rows, refs)), 1)


if __name__ == '__main__':
    unittest.main()
