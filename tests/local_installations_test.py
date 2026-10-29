"""Application detection without shell PATH; installed/running/catalog are distinct states."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import llm


class LocalInstallations(unittest.TestCase):
    def test_mac_app_bundles_in_system_and_user_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            system=Path(tmp)/'Applications';user=Path(tmp)/'UserApplications'
            (system/'Ollama.app').mkdir(parents=True);(user/'LM Studio.app').mkdir(parents=True)
            self.assertTrue(llm._mac_app_installed('Ollama',[system,user]))
            self.assertTrue(llm._mac_app_installed('LM Studio',[system,user]))
            self.assertFalse(llm._mac_app_installed('Missing',[system,user]))

    def test_mac_gui_detects_both_apps_without_cli_in_path(self):
        with patch('llm.sys.platform','darwin'),patch('shutil.which',return_value=None),patch('llm._mac_app_installed',return_value=True):
            self.assertTrue(llm.ollama_installed())
            self.assertTrue(llm.lmstudio_installed())

    def test_installed_app_with_stopped_server_is_not_reported_missing(self):
        with patch('llm.ollama_installed',return_value=True),patch('llm.lmstudio_installed',return_value=True),patch('llm._models_at',return_value=None),patch('llm.lmstudio_start') as start:
            result=llm.local_installations()
            self.assertTrue(all(r['installed'] and not r['running'] for r in result))
            start.assert_not_called()

    def test_empty_catalog_means_running_server(self):
        with patch('llm.ollama_installed',return_value=False),patch('llm.lmstudio_installed',return_value=False),patch('llm._models_at',side_effect=[[],['local/model']]):
            result=llm.local_installations()
            self.assertTrue(all(r['installed'] and r['running'] for r in result))
            self.assertEqual(result[0]['models'],[])
            self.assertEqual(result[1]['models'],['local/model'])

    def test_missing_program_and_server(self):
        with patch('llm.ollama_installed',return_value=False),patch('llm.lmstudio_installed',return_value=False),patch('llm._models_at',return_value=None):
            self.assertTrue(all(not r['installed'] and not r['running'] for r in llm.local_installations()))


if __name__=='__main__':unittest.main()
