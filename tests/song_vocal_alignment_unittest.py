"""Failed mixture onset must not swallow a break or move a later chorus."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.onset_check import retry_stem_alignment


class VocalAlignmentTests(unittest.TestCase):
    def run_retry(self, *, shift=0, voiced_pause=False, partial=False, no_stem=False):
        text='Где-то через музыку ветра'
        original=[dict(text='Где-то',char_start=0,char_end=6,start_ms=1000,end_ms=12120),
                  dict(text='музыку',char_start=13,char_end=19,start_ms=12120,end_ms=12280),
                  dict(text='ветра',char_start=20,char_end=25,start_ms=12760,end_ms=13320)]
        triples=[('Гдето',9600,10100),('через',11720,12120),
                 ('музыку',12120,12680),('ветра',12680,13400)]
        def units(offset,delta=0):
            return [SimpleNamespace(text=t,start_time=(a-offset+delta)/1000,
                                    end_time=(b-offset+delta)/1000) for t,a,b in triples]
        model=Mock()
        model.align.side_effect=[[units(0)], [units(240,shift)[:-1] if partial else units(240,shift)]]
        stem=np.ones(14000)
        if not voiced_pause:stem[1000:9500]=0
        result,report=retry_stem_alignment(model,text,original,0,14000,
                                          None if no_stem else (stem,1000))
        return result,report,original,model

    def test_stable_vocal_retry_restores_missing_word_and_break(self):
        result,report,_,model=self.run_retry()
        self.assertTrue(report['applied'])
        self.assertEqual(result[0]['start_ms'],9600)
        self.assertEqual([w['text'] for w in result],['Где-то','через','музыку','ветра'])
        self.assertEqual(model.align.call_count,2)

    def test_unstable_or_incomplete_retry_keeps_original(self):
        for options in ({'shift':500},{'partial':True},{'voiced_pause':True}):
            with self.subTest(options):
                result,report,original,_=self.run_retry(**options)
                self.assertFalse(report['applied'])
                self.assertEqual(result,original)

    def test_missing_stem_does_not_download_or_infer(self):
        result,report,original,model=self.run_retry(no_stem=True)
        self.assertEqual(result,original)
        self.assertIsNone(report)
        model.align.assert_not_called()


if __name__=='__main__':unittest.main()
