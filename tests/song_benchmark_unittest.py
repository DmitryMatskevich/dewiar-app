"""Метрики пилота вычисляются только относительно проверенного эталона."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('songs_benchmark',Path(__file__).resolve().parents[1]/'tools/songs_benchmark.py')
bench=importlib.util.module_from_spec(spec); spec.loader.exec_module(bench)


class BenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.audio=self.root/'audio.wav'; self.audio.write_bytes(b'fixture')
        self.draft=self.root/'draft.json'; self.frozen=self.root/'frozen.json'; self.hypo=self.root/'hypothesis.json'
        self.ref={'audio_sha256':bench.digest(self.audio),'reviewed_audio_complete':True,'reviewer':'unit-test',
                  'segments':[{'start_ms':0,'end_ms':1000,'text':'Ёж идёт домой','reviewed':True}]}
        self.draft.write_text(json.dumps(self.ref)); self.hypo.write_text(json.dumps({'text':'еж идет домой'}))

    def test_unreviewed_draft_cannot_be_reference(self):
        self.ref['segments'][0]['reviewed']=False; self.draft.write_text(json.dumps(self.ref))
        with self.assertRaisesRegex(ValueError,'непроверенные'):
            bench.freeze(self.draft,self.audio,self.frozen)
        self.assertFalse(self.frozen.exists())

    def test_normalization_and_known_word_error(self):
        bench.freeze(self.draft,self.audio,self.frozen)
        self.assertEqual(bench.score(self.frozen,self.hypo,self.audio)['wer'],0)
        self.hypo.write_text(json.dumps({'text':'еж бежит домой'}))
        self.assertAlmostEqual(bench.score(self.frozen,self.hypo,self.audio)['wer'],1/3)

    def test_modified_reference_rejected(self):
        bench.freeze(self.draft,self.audio,self.frozen)
        obj=json.loads(self.frozen.read_text()); obj['reference']['segments'][0]['text']='подмена'
        self.frozen.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError,'изменён'): bench.score(self.frozen,self.hypo,self.audio)

    def test_different_audio_rejected(self):
        bench.freeze(self.draft,self.audio,self.frozen); self.audio.write_bytes(b'another version')
        with self.assertRaisesRegex(ValueError,'Другая версия'): bench.score(self.frozen,self.hypo,self.audio)

    def test_existing_reference_not_overwritten(self):
        bench.freeze(self.draft,self.audio,self.frozen)
        original=self.frozen.read_bytes()
        with self.assertRaises(FileExistsError): bench.freeze(self.draft,self.audio,self.frozen)
        self.assertEqual(self.frozen.read_bytes(),original)


if __name__ == '__main__': unittest.main()
