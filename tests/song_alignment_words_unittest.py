import sys
import unittest
import tempfile
import copy
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace as Unit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.alignment_words import measured_words
from songs.worker import _alignment_windows,align_intro_from_vocals
from songs.chord_layout import _vocal_bounds


def units(*items):
    return [Unit(text=t,start_time=a,end_time=b) for t,a,b in items]


class AlignmentWordsTests(unittest.TestCase):
    def test_syllables_keep_full_hyphenated_word_and_times(self):
        text='Мы по-прежнему здесь.'
        words=measured_words(text,units(('Мы',0,.5),('по',.5,.8),('прежнему',.8,1.5),('здесь',1.5,2)),1000,3000)
        self.assertEqual([w['text'] for w in words],text.split())
        self.assertEqual((words[1]['start_ms'],words[1]['end_ms']),(1500,2500))
        self.assertEqual(text[words[1]['char_start']:words[1]['char_end']],'по-прежнему')

    def test_zero_duration_does_not_throw_away_other_measured_words(self):
        words=measured_words('мы идём домой',units(('мы',0,.4),('идём',.4,.4),('домой',.7,1)),0,1000)
        self.assertEqual([w['text'] for w in words],['мы','домой'])

    def test_mismatch_and_fused_units_do_not_create_word_times(self):
        self.assertEqual(measured_words('мы здесь',units(('мы',0,.4),('там',.5,1)),0,1000),[])
        self.assertEqual(measured_words('мы здесь',units(('мыздесь',0,1)),0,1000),[])

    def test_nan_and_out_of_window_are_rejected(self):
        self.assertEqual(measured_words('слово',units(('слово',0,float('nan'))),0,1000),[])
        self.assertEqual(measured_words('слово',units(('слово',0,2)),0,1000),[])

    def test_distant_chorus_cannot_expand_alignment_window(self):
        phrase=dict(text='Одна и та же строка припева',start_ms=10000,end_ms=15000)
        context=[{'phrases':[dict(phrase,start_ms=100000,end_ms=110000)]}]
        self.assertEqual(_alignment_windows([phrase],context),[(10000,15000)])

    def test_matching_text_in_coarse_chunk_cannot_pull_vocals_into_intro(self):
        phrase=dict(text='Улыбнулся прохожему на улице',start_ms=30000,end_ms=35000)
        context=[{'phrases':[dict(phrase,start_ms=25000,end_ms=50000,timing_quality='chunk')]}]
        self.assertEqual(_alignment_windows([phrase],context),[(30000,35000)])

    def test_partial_alignment_keeps_unmeasured_first_and_last_words(self):
        phrase=dict(text='мы идём домой',start_ms=0,end_ms=3000,timing_quality='word_partial',
                    words=[dict(text='идём',char_start=3,char_end=7,start_ms=1000,end_ms=1800)])
        self.assertEqual(_vocal_bounds(phrase),(0,3000))

    def test_stem_energy_cannot_move_intro_past_first_phrase_or_erase_words(self):
        for onset in (2000,8600):
            with self.subTest(onset=onset),tempfile.TemporaryDirectory() as tmp:
                folder=Path(tmp)/'stem';folder.mkdir();vocals=folder/'vocals.wav'
                phrase=dict(text='Первая строка',start_ms=0,end_ms=5000,words=[],timing_quality='phrase')
                selected={'phrases':[copy.deepcopy(phrase)]}
                with patch('songs.worker._separate_vocals_for_intro',return_value=vocals),patch(
                        'songs.worker.align_phrases'),patch('songs.worker._vocal_onset_ms',return_value=onset):
                    self.assertFalse(align_intro_from_vocals('audio','output',selected,[],lambda _:None,0))
                self.assertEqual(selected['phrases'][0]['start_ms'],0)
                self.assertEqual(selected['phrases'][0]['end_ms'],5000)
                self.assertEqual(selected['phrases'][0]['text'],phrase['text'])


if __name__=='__main__':unittest.main()
