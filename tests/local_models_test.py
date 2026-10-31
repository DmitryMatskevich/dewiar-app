"""Shared model inventory and explicit, model-preserving activation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import local_models as M
import strata_local as SL

class Models(unittest.TestCase):
    def test_offline_lms_variants_and_embedding_exclusion(self):
        output = [{'type':'llm','displayName':'Flint','modelKey':'recursivesi/flint',
                   'variants':['recursivesi/flint@q4','recursivesi/flint@q8'],'sizeBytes':1024**3},
                  {'type':'embedding','modelKey':'embedding'}]
        with patch('llm.lmstudio_cli',return_value='/fixture/lms'), patch.object(M.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(output))) as run:
            rows = M.lmstudio_models()
        self.assertEqual([r['model'] for r in rows], ['recursivesi/flint@q4','recursivesi/flint@q8'])
        self.assertEqual(run.call_args.args[0], ['/fixture/lms','ls','--json'])

    def test_inventory_contains_offline_lms_and_strata_without_start(self):
        strata = Mock(package_id='')
        strata.status.return_value = {'state':'stopped','catalog':[{'id':'qwen','name':'Qwen','ready':True,'disk_gib':63}], 'runtime':True}
        with patch('llm.local_installations',return_value=[{'preset':'lmstudio','name':'LM Studio','models':[], 'running':False,'installed':True,'base_url':'http://localhost:1234/v1'}]), patch.object(M,'lmstudio_models',return_value=[{'model':'flint@q4','name':'Flint'}]):
            data = M.inventory(strata)
        self.assertEqual([r['name'] for r in data['models']], ['Flint','Qwen'])
        self.assertEqual(data['models'][0]['state'],'downloaded')
        strata.start.assert_not_called()

    def test_connect_preserves_selected_variant_and_custom_port(self):
        with patch.object(M,'inventory',return_value={'models':[{'source':'lmstudio','model':'flint@q4','base_url':'old','app':'LM Studio'}]}), patch('llm.lmstudio_start',return_value=('http://127.0.0.1:4567/v1',False)), patch('llm._models_at',return_value=['flint']):
            profile = M.connect(Mock(),'lmstudio','flint@q4')
            self.assertEqual(profile['model'],'flint@q4')
            self.assertEqual(profile['base_url'],'http://127.0.0.1:4567/v1')
            with self.assertRaises(ValueError):M.connect(Mock(),'lmstudio','other')

    def test_ollama_offline_manifests_require_complete_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'OLLAMA_MODELS':tmp}):
            root=Path(tmp); path=root/'manifests/registry.ollama.ai/library/test/q4';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'layers':[{'digest':'sha256:abc','size':100}]}))
            self.assertEqual(M.ollama_downloads(),[])
            (root/'blobs').mkdir();(root/'blobs/sha256-abc').touch()
            self.assertEqual(M.ollama_downloads()[0]['model'],'test:q4')

    def test_details_preserve_catalog_data_when_api_offline(self):
        import httpx
        row = {'source':'lmstudio', 'app':'LM Studio','name':'Muse Glimmer', 'model':'meta/muse-glimmer@q4',
               'catalog_id':'meta/muse-glimmer','parameters':'28B','context':131072,'base_url':'http://localhost/v1'}
        with patch.object(M,'inventory',return_value={'models':[row]}), patch.object(M.httpx,'get',side_effect=httpx.ConnectError('offline')):
            info = M.details(Mock(),'lmstudio',row['model'])
        self.assertEqual(info['parameters'],'28B')
        self.assertEqual(info['catalog_url'],'https://lmstudio.ai/models/meta/muse-glimmer')
        self.assertIn('metadata_note',info)

    def test_details_distinguish_model_and_loaded_context(self):
        row = {'source':'lmstudio','app':'LM Studio','name':'Flint','model':'recursivesi/flint@q4',
               'catalog_id':'recursivesi/flint','context':262144,'base_url':'http://localhost/v1'}
        response=Mock();response.json.return_value={'models':[{'key':'recursivesi/flint',
            'capabilities':{'reasoning':{'allowed_options':['on']}},
            'loaded_instances':[{'config':{'context_length':8192}}]}]}
        with patch.object(M,'inventory',return_value={'models':[row]}), patch.object(M.httpx,'get',return_value=response):
            info=M.details(Mock(),'lmstudio',row['model'])
        self.assertEqual(info['context'],262144)
        self.assertEqual(info['loaded_contexts'],[8192])
        self.assertTrue(info['reasoning'])

    def test_open_app_uses_known_names_and_foregrounds_on_mac(self):
        with patch('sys.platform','darwin'), patch.object(M.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(M.open_app('lmstudio')['app'],'LM Studio')
            self.assertEqual(run.call_args.args[0],['/usr/bin/open','-a','LM Studio'])
            M.open_app('strata_api')
            self.assertEqual(run.call_args.args[0],['/usr/bin/open','-a','Strata'])
            with self.assertRaises(ValueError):M.open_app('anything; touch /tmp/no')
            self.assertEqual(run.call_count,2)

    def test_open_app_reports_missing_installation(self):
        with patch('sys.platform','darwin'), patch.object(M.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError,'установлено'):M.open_app('ollama')

    def test_auto_without_strata_reports_missing_app(self):
        with patch.object(SL,'discover_desktop_api',return_value=''):
            with self.assertRaisesRegex(ValueError,'не запущена'):SL.probe('auto')

if __name__ == '__main__':unittest.main()
