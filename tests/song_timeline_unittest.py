"""Regression: recovered suffix before prefix, and lyrics swallowing a break.

Only isolated dictionaries and fake alignment/audio evidence; no app/settings.
"""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.consensus import recover_omissions
from songs.phrase_timeline import ordered_phrases, alignment_groups
from songs.chord_layout import build_guitar_sheet
from songs.sheet_lines import readable_sheet
from songs.onset_check import onset_supported


def phrase(pid,text,a,b,**kw):
    return dict(phrase_id=pid,text=text,start_ms=a,end_ms=b,**kw)


class TimelineTests(unittest.TestCase):
    def test_local_suffix_is_inserted_before_the_next_global_phrase(self):
        original=[phrase('first','Как много тех',0,10000),phrase('next','Другая строка',30000,34000)]
        base={'model':'gigaam','phrases':[phrase('g','Как много тех с кем можно лечь в постель',0,21900)]}
        rows=recover_omissions(original,[base,dict(base,model='qwen')])
        self.assertEqual([p['text'] for p in rows],['Как много тех','с кем можно лечь в постель','Другая строка'])
        self.assertEqual(rows[1]['recovery_order'],{'after_phrase_id':'first','before_phrase_id':'next'})
        self.assertEqual(len(alignment_groups(rows)[0]),2)

    def test_old_appended_suffix_uses_source_text_order_not_coarse_time(self):
        prefix=phrase('first','Как много тех',16640,18800)
        following=phrase('next','Другая строка',21900,30000)
        evidence=dict(models=['gigaam'],text='с кем можно лечь в постель',start_ms=0,end_ms=21900)
        suffix=phrase('coverage','с кем можно лечь в постель',10000,21900,
                      recovered_spans=[evidence],alignment_stale=True)
        original=[prefix,following,suffix];before=copy.deepcopy(original)
        hypotheses=[{'model':'gigaam','phrases':[phrase('g','Как много тех с кем можно лечь в постель',0,21900)]}]
        rows=ordered_phrases(original,hypotheses)
        self.assertEqual([p['phrase_id'] for p in rows],['first','coverage','next'])
        self.assertEqual(rows[1]['start_ms'],18800)
        self.assertEqual(rows,ordered_phrases(rows,hypotheses))
        self.assertEqual(original,before)

    def test_distant_repeat_and_unsupported_legacy_data_are_not_reordered(self):
        rows=[phrase('first','Первая строка',100000,102000),phrase('tail','Три новых слова',0,5000,
                recovered_spans=[dict(models=['gigaam'],text='Три новых слова',start_ms=0,end_ms=5000)])]
        context=[{'model':'gigaam','phrases':[phrase('g','Первая строка Три новых слова',0,5000)]}]
        self.assertEqual(ordered_phrases(rows,context),rows)

    def test_vocal_start_not_segment_start_determines_line_order(self):
        p=phrase('p','Вот так',112000,134500,words=[
            dict(text='Вот',char_start=0,char_end=3,start_ms=130000,end_ms=130400),
            dict(text='так',char_start=4,char_end=7,start_ms=130400,end_ms=130800)])
        events=[dict(name='G',start_ms=115000,end_ms=120000),dict(name='Bm',start_ms=120000,end_ms=135000)]
        rows=build_guitar_sheet([p],events)
        self.assertEqual(rows[-1]['text'],'Вот так')
        self.assertEqual(rows[-1]['start_ms'],130000)
        self.assertTrue(all(not r['text'] for r in rows[:-1]))

    def test_measured_internal_pause_keeps_source_offsets_for_editing(self):
        p=phrase('p','Первые слова После паузы',1000,11000,words=[
            dict(char_start=0,char_end=6,start_ms=1000,end_ms=1800),
            dict(char_start=7,char_end=12,start_ms=1800,end_ms=2500),
            dict(char_start=13,char_end=18,start_ms=9000,end_ms=9800),
            dict(char_start=19,char_end=24,start_ms=9800,end_ms=11000)])
        events=[dict(name='Am',start_ms=0,end_ms=6000),dict(name='E7',start_ms=6000,end_ms=12000)]
        rows=readable_sheet(build_guitar_sheet([p],events))
        text=[r for r in rows if r['text']]
        self.assertEqual([r['text'] for r in text],['Первые слова','После паузы'])
        self.assertEqual(text[1]['source_spans'][0]['phrase_char_start'],13)
        self.assertEqual(text[1]['display_words'][0]['phrase_char_start'],13)
        self.assertTrue(any(not r['text'] and 2500<=r['start_ms']<9000 for r in rows))

    def test_sustained_chord_does_not_erase_instrumental_gap(self):
        rows=build_guitar_sheet([phrase('a','До паузы',1000,2000),phrase('b','После паузы',7000,9000)],
                               [dict(name='Am',start_ms=0,end_ms=10000)])
        self.assertTrue(any(not r['text'] and r['start_ms']==2000 and r['end_ms']==7000 for r in rows))

    def test_long_note_is_not_shortened_without_quiet_vocal_evidence(self):
        import numpy as np
        original=[dict(char_start=0,char_end=3,start_ms=0,end_ms=11000),
                  dict(char_start=4,char_end=7,start_ms=11000,end_ms=11500),
                  dict(char_start=8,char_end=9,start_ms=11500,end_ms=12000)]
        trial=[dict(original[0],start_ms=10500),*original[1:]]
        self.assertFalse(onset_supported(original,[trial,trial],None))
        self.assertFalse(onset_supported(original,[trial,trial],(np.ones(13000),1000)))
        stem=np.ones(13000);stem[9500:10400]=0
        self.assertTrue(onset_supported(original,[trial,trial],(stem,1000)))
        different=[dict(trial[0],start_ms=10000),*trial[1:]]
        self.assertFalse(onset_supported(original,[trial,different],(stem,1000)))


if __name__=='__main__':unittest.main()
