"""Offline regressions for a first lyric wrongly timestamped inside the intro."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
from songs.intro_alignment import matching_words, recover_intro, bounds_with_words, repair_opening_insertions
from songs.chord_layout import build_guitar_sheet


def decoded(start=23):
    return {'segments':[{'words':[dict(word=t,start=start+i*.5,end=start+(i+1)*.5)
                                  for i,t in enumerate(['Первая','строка','песни'])]}]}


class IntroAlignmentTests(unittest.TestCase):
    def test_clipped_window_is_recovered_without_changing_text(self):
        phrase=dict(text='Первая строка песни',start_ms=0,end_ms=10000)
        original=copy.deepcopy(phrase)
        model=Mock();model.align.return_value=[[SimpleNamespace(text=t,start_time=1.2+i*.5,
                    end_time=1.2+(i+1)*.5) for i,t in enumerate(phrase['text'].split())]]
        decode=Mock(side_effect=[decoded(),decoded()])
        words,report=recover_intro(model,[0]*35000,1000,([0]*35000,1000),phrase,30000,decode)
        self.assertTrue(report['applied']);self.assertEqual(phrase,original)
        phrase.update(words=words,timing_quality='word_estimated')
        phrase['start_ms'],phrase['end_ms']=bounds_with_words(phrase,words)
        rows=build_guitar_sheet([phrase],[dict(name='Em',start_ms=0,end_ms=24000),
                                       dict(name='C',start_ms=24000,end_ms=25000)])
        intro=rows[0];lyric=next(r for r in rows if r['text'])
        self.assertTrue(intro['instrumental']);self.assertEqual(intro['end_ms'],23000)
        self.assertEqual(lyric['start_ms'],23000)
        self.assertEqual([c['name'] for c in intro['unplaced_chords']],['Em'])

    def test_partial_alignment_never_discards_outside_measured_words(self):
        phrase=dict(text='Первая строка песни',start_ms=30000,end_ms=35000,timing_quality='word_partial',
                    words=[dict(char_start=7,char_end=13,start_ms=24920,end_ms=25240)])
        self.assertEqual(bounds_with_words(phrase,phrase['words']),(24920,35000))
        self.assertEqual(next(r for r in build_guitar_sheet([phrase],[]) if r['text'])['start_ms'],24920)

    def test_forced_first_word_stretched_to_crop_start_uses_cross_view_onset(self):
        model=Mock();model.align.return_value=[[SimpleNamespace(text=t,start_time=0 if i==0 else 1.2+i*.5,
                    end_time=1.2+(i+1)*.5) for i,t in enumerate(['Первая','строка','песни'])]]
        words,report=recover_intro(model,[0]*35000,1000,([0]*35000,1000),
                 dict(text='Первая строка песни',start_ms=0,end_ms=10000),30000,
                 Mock(side_effect=[decoded(),decoded()]))
        self.assertTrue(report['applied']);self.assertEqual(words[0]['start_ms'],23000)
        self.assertEqual(words[0]['method'],'whisper_cross_view_onset_qwen_tail')

    def test_lead_in_is_before_first_phrase_when_first_word_starts_after_stem_onset(self):
        phrase = dict(
            phrase_id='opening', text='То ли дернул меня без,', start_ms=16000,
            end_ms=24960, timing_quality='word_estimated',
            words=[
                dict(text='То', char_start=0, char_end=2, start_ms=16000, end_ms=16160),
                dict(text='ли', char_start=3, char_end=5, start_ms=16160, end_ms=16320),
            ],
        )
        rows = build_guitar_sheet([phrase], [
            dict(name='Cm7', start_ms=11221, end_ms=16811),
            dict(name='Ab', start_ms=16200, end_ms=18048),
        ], duration_ms=24960)
        self.assertTrue(rows[0]['instrumental'])
        self.assertEqual((rows[0]['start_ms'], rows[0]['end_ms']), (0, 16000))
        self.assertEqual(rows[1]['text'], phrase['text'])
        self.assertEqual(rows[1]['start_ms'], 16000)
        self.assertEqual([c['name'] for c in rows[0]['unplaced_chords']], ['Cm7'])
        self.assertEqual([c['name'] for c in rows[1]['chords']], ['Cm7', 'Ab'])

    def test_repeated_lyrics_and_zero_length_are_not_onset_evidence(self):
        value=decoded();value['segments']*=2
        self.assertFalse(matching_words('Первая строка песни',value))
        value=decoded();value['segments'][0]['words'][0]['end']=23
        self.assertFalse(matching_words('Первая строка песни',value))

    def test_disagreeing_views_do_not_move_phrase(self):
        model=Mock()
        words,report=recover_intro(model,[0]*35000,1000,([0]*35000,1000),
                 dict(text='Первая строка песни',start_ms=0,end_ms=10000),30000,
                 Mock(side_effect=[decoded(),decoded(24)]))
        self.assertIsNone(words);self.assertFalse(report['applied']);model.align.assert_not_called()

    def test_missing_stem_does_not_start_decoder(self):
        decoder=Mock()
        self.assertIsNone(recover_intro(Mock(),[],1000,None,{},None,decoder)[0])
        decoder.assert_not_called()

    def test_newly_located_lyrics_are_not_mistaken_for_an_instrumental_gap(self):
        rows=[dict(phrase_id='first',text='Первая строка',start_ms=23000,end_ms=24000),
              dict(phrase_id='next',text='А дальше вторая строка',start_ms=28000,end_ms=32000)]
        text='Первая строка Тут были пропущены слова А дальше вторая строка'
        tokens=list(__import__('re').finditer(r'\S+',text))
        words=[dict(text=t.group(),char_start=t.start(),char_end=t.end(),start_ms=23000+i*800,
                    end_ms=23500+i*800) for i,t in enumerate(tokens)]
        w=dict(model='Whisper-large-v2',phrases=[dict(text=text,start_ms=23000,end_ms=32000,words=words)])
        q=dict(model='Qwen3-ASR',phrases=[dict(text=text,start_ms=22000,end_ms=34000)])
        result,report=repair_opening_insertions(rows,w,q)
        self.assertTrue(report['applied'])
        self.assertEqual(result[1]['text'],'Тут были пропущены слова А дальше вторая строка')
        self.assertEqual(result[1]['start_ms'],24600)
        self.assertEqual(result[0],rows[0])
        rows[1]['manual_edited']=True
        self.assertEqual(repair_opening_insertions(rows,w,q)[0],rows)


if __name__=='__main__':unittest.main()
