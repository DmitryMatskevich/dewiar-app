import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import model_warmup as W

P={'kind':'openai','base_url':'http://127.0.0.1:9999/v1','model':'flint@q4'}

class Warmup(unittest.TestCase):
    def test_cloud_and_remote_never_preloaded(self):
        for p in [None,dict(P,cloud=True),dict(P,model='name:cloud'),dict(P,base_url='https://example.com/v1'),dict(P,kind='codex_subscription')]:
            self.assertFalse(W.local_profile(p))
        self.assertTrue(W.local_profile(P))

    def test_lm_load_then_one_token_without_history(self):
        calls=[]
        def handle(req):
            body=json.loads(req.content) if req.content else {}
            calls.append((req.url.path,body))
            if req.method=='GET':return httpx.Response(200,json={'models':[{'key':'flint','loaded_instances':[]}]})
            return httpx.Response(200,json={'choices':[{'message':{'content':'Hi'}}]})
        client=httpx.Client(transport=httpx.MockTransport(handle))
        with patch.object(W.httpx,'Client',return_value=client):W.preload(P,threading.Event())
        self.assertEqual([x[0] for x in calls],['/api/v1/models','/api/v1/models/load','/v1/chat/completions'])
        self.assertEqual(calls[-1][1]['model'],'flint@q4')
        self.assertEqual(calls[-1][1]['max_tokens'],1)
        self.assertEqual(calls[1][1]['context_length'],4096)

    def test_ollama_uses_empty_prompt_keepalive(self):
        calls=[]
        def handle(req):
            calls.append(json.loads(req.content));return httpx.Response(200,json={'done':True})
        with patch.object(W.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(handle))):
            W.preload(dict(P,kind='ollama'),threading.Event())
        self.assertEqual(calls,[{'model':'flint@q4','prompt':'','stream':False,'keep_alive':'10m'}])

    def test_loaded_lm_keeps_existing_context(self):
        paths=[]
        def handle(req):
            paths.append(req.url.path)
            if req.method=='GET':return httpx.Response(200,json={'models':[{'key':'flint','loaded_instances':[{'config':{'context_length':8192}}]}]})
            return httpx.Response(200,json={'choices':[{}]})
        with patch.object(W.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(handle))):W.preload(P,threading.Event())
        self.assertNotIn('/api/v1/models/load',paths)

    def test_cancelled_preload_sends_nothing(self):
        stop=threading.Event();stop.set()
        with patch.object(W.httpx,'Client') as client:
            W.preload(P,stop)
            client.return_value.__enter__.return_value.post.assert_not_called()

    def engine(self):
        e=Mock();e.stopping=threading.Event();e.s.active_llm.return_value=dict(P);e.status={'loading':{}}
        e._load.side_effect=lambda k,v:e.status['loading'].update({k:v})
        e._loaded.side_effect=lambda k:e.status['loading'].pop(k,None)
        return e

    def test_selected_only_deduplicated_and_switch_warms(self):
        e=self.engine();service=W.ModelWarmup(e)
        with patch.object(W,'preload') as load:
            service.step();service.step();self.assertEqual(load.call_count,1)
            self.assertEqual(e.status['model_warmup']['state'],'ready')
            e.s.active_llm.return_value=dict(P,model='muse');service.step()
            self.assertEqual(load.call_args.args[0]['model'],'muse')
        self.assertFalse(e.status['loading'])

    def test_failure_does_not_change_routing_and_is_retried(self):
        e=self.engine();service=W.ModelWarmup(e)
        with patch.object(W,'preload',side_effect=ValueError('failed')) as load:
            service.step();service.step();self.assertEqual(load.call_count,1)
            self.assertEqual(e.status['model_warmup']['state'],'error')
            service.at-=31;service.step();self.assertEqual(load.call_count,2)
        e.s.update.assert_not_called();self.assertFalse(e.status['loading'])

    def test_profile_switch_during_load_does_not_report_old_ready(self):
        e=self.engine();service=W.ModelWarmup(e)
        with patch.object(W,'preload',side_effect=lambda *_:setattr(e.s.active_llm,'return_value',dict(P,model='muse'))):service.step()
        self.assertFalse(service.ready);e.probe_tools_bg.assert_not_called()

if __name__=='__main__':unittest.main()
