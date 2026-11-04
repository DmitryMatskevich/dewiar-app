"""Загрузка ASR для песен не скачивает модели и не чинит общий кэш."""
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy  # сохраняем загруженный C-модуль вне временного sys.modules

ROOT = Path(__file__).resolve().parents[1]


class CachedModelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('isolated_song_stt', ROOT/'core/stt.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'settings': types.SimpleNamespace(MODELS_DIR=self.root)}):
            spec.loader.exec_module(self.module)
        self.model = self.module.GigaAMSTT()
        self.model._drop_hub = Mock(side_effect=AssertionError('must not delete cache'))
        self.model._from_mirror = Mock(side_effect=AssertionError('must not download'))

    def test_missing_model_returns_error_without_loading_library(self):
        loader = Mock(side_effect=AssertionError('must not run loader'))
        with patch.dict(sys.modules, {'onnx_asr': types.SimpleNamespace(load_model=loader)}):
            self.assertFalse(self.model.load_cached())
        loader.assert_not_called()
        self.assertIn('скачивание', self.model.error)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_explicit_local_path_used_and_corrupt_cache_preserved(self):
        folder = self.model.local_dir; folder.mkdir()
        (folder/'v3_e2e_rnnt_encoder.int8.onnx').write_bytes(b'fixture')
        (folder/'vocab.txt').write_text('fixture')
        before = {p.name:p.read_bytes() for p in folder.iterdir()}
        loader = Mock(side_effect=RuntimeError('incomplete decoder'))
        with patch.dict(sys.modules, {'onnx_asr': types.SimpleNamespace(load_model=loader),
                'onnxruntime': types.SimpleNamespace(SessionOptions=types.SimpleNamespace)}):
            self.assertFalse(self.model.load_cached())
        self.assertEqual(loader.call_args.args[1], str(folder))
        self.assertEqual({p.name:p.read_bytes() for p in folder.iterdir()}, before)
        self.model._drop_hub.assert_not_called()
        self.model._from_mirror.assert_not_called()


if __name__ == '__main__': unittest.main()
