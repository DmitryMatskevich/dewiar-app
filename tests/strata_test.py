"""Strata integration without loading large weights: protocol, profile guard and owned lifecycle."""
import json
import os
import sys
import tempfile
import time
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import strata_local as SL
from llm import stream_chat,stream_events


class API(BaseHTTPRequestHandler):
    requests=[]
    def log_message(self,*args):pass
    def do_GET(self):
        if self.headers.get('Authorization')!='Bearer fixture':self.send_response(401);self.end_headers();return
        self.send_response(200);self.end_headers();self.wfile.write(b'{"data":[{"id":"strata-one"},{"id":"strata-two"}]}')
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));type(self).requests.append(body)
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        delta={'content':'Ответ Strata: связь работает.'}
        if body.get('tools'):delta={'tool_calls':[{'index':0,'id':'fixture','type':'function','function':{'name':'calc','arguments':'{"expression":"2+2"}'}}]}
        self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta}]})+'\n\ndata: [DONE]\n\n').encode())


class Strata(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.control=self.home/'.local/share/strata/terminal/current';(self.control/'python/bin').mkdir(parents=True)
        (self.control/'python/bin/python3').symlink_to(sys.executable)
        (self.control/'data').mkdir();(self.control/'data/expert-profile.bin').write_bytes(b'x')
        (self.control/'strata.py').write_text('')
        self.pack=self.home/'pack';self.pack.mkdir();(self.pack/'tokenizer').mkdir()
        self.native=self.pack/'model-00001.gguf';self.native.write_bytes(b'GGUF-fixture')
        self.ple=self.pack/'model-00002.gguf';self.ple.write_bytes(b'GGUF-fixture')
        folder=self.home/'.local/share/strata/install';folder.mkdir(parents=True)
        self.config=folder/'strata-fixture.json'
        self.value={'exe':'component:strata-engine','model_name':'fixture','tokenizer':str(self.pack/'tokenizer'),
                    'args':['--native',str(self.native),'--pack',str(self.pack),'--ple-gguf',str(self.ple),'--max-context','524288','--vision','--spec','2','--mtp','not-downloaded'],
                    'env':{'DYLD_INSERT_LIBRARIES':'bad','STRATA_METAL_STREAM':'1'},'before_load':['bad'],'mcp_servers':{'bad':{}},'api_key':'foreign-key'}
        self.config.write_text(json.dumps(self.value))
        self.manager=SL.StrataLocal(self.home/'state',self.home);self.addCleanup(self.manager.stop)

    def test_catalog_is_read_only_and_complete(self):
        with patch('strata_local.subprocess.Popen') as spawn:
            data=self.manager.status();spawn.assert_not_called()
        self.assertTrue(data['runtime']);self.assertTrue(data['catalog'][0]['ready'])
        self.assertNotIn('foreign-key',json.dumps(data));self.assertNotIn('before_load',json.dumps(data))
        self.native.unlink();self.assertFalse(self.manager.status()['catalog'][0]['ready'])

    def test_config_copy_keeps_original_and_removes_execution_hooks(self):
        cfg=self.manager._config(self.config,self.control,4096)
        self.assertEqual(cfg['args'][cfg['args'].index('--max-context')+1],'4096')
        self.assertNotIn('--vision',cfg['args']);self.assertNotIn('--mtp',cfg['args'])
        self.assertEqual(cfg['args'][cfg['args'].index('--spec')+1],'0')
        self.assertNotIn('before_load',cfg);self.assertNotIn('mcp_servers',cfg);self.assertNotIn('api_key',cfg)
        self.assertEqual(cfg['env'],{'STRATA_METAL_STREAM':'1'})
        self.assertEqual(json.loads(self.config.read_text()),self.value)

    def test_memory_guard_rejects_before_spawning(self):
        item=self.manager.status()['catalog'][0]
        with patch('strata_local.psutil.virtual_memory',return_value=SimpleNamespace(available=1024)),patch('strata_local.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'памяти') as caught:self.manager.start(item['id'])
            self.assertEqual(type(caught.exception).__name__,'MemoryShortage')
            spawn.assert_not_called()

    def test_invalid_id_context_and_custom_exe(self):
        with self.assertRaises(ValueError):self.manager.start('outside')
        for context in (True,0,100000):
            with self.assertRaises(ValueError):self.manager.start('outside',context)
        self.value['exe']='/bin/sh';self.config.write_text(json.dumps(self.value))
        with self.assertRaisesRegex(ValueError,'исполняемые'):self.manager._config(self.config,self.control,4096)

    def test_api_models_auth_streaming_and_tool_ownership(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),API);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}/v1'
        with self.assertRaises(ValueError):SL.probe(base,'wrong')
        profile=SL.probe(base,'fixture');self.assertEqual(profile['models'],['strata-one','strata-two'])
        profile['model']='strata-one'
        self.assertIn('связь работает',''.join(stream_chat(profile,'Тест',[{'role':'user','content':'Тест'}])))
        self.assertIs(API.requests[-1]['strata_mcp'],False)
        tools=[{'name':'calc','description':'calculate','parameters':{'type':'object','properties':{'expression':{'type':'string'}}}}]
        events=list(stream_events(profile,'Тест',[{'role':'user','content':'Тест'}],tools=tools))
        self.assertTrue(any(kind=='tools' for kind,_ in events),events)
        self.assertIs(API.requests[-1]['strata_mcp'],False)

    def test_desktop_discovery_replaces_auto_only_and_reports_empty_engine(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),API);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        discovered=f'http://127.0.0.1:{server.server_port}/v1'
        with patch('strata_local.discover_desktop_api',return_value=discovered):
            profile=SL.probe('auto','fixture')
            self.assertEqual(profile['base_url'],discovered)
            self.assertTrue(profile['discovered'])
            self.assertEqual(profile['api_state'],'ready')

    def test_owned_process_launch_and_stop_without_touching_foreign_servers(self):
        script='''import argparse,json
from http.server import BaseHTTPRequestHandler,HTTPServer
p=argparse.ArgumentParser();p.add_argument('--profile');p.add_argument('--port',type=int);a,_=p.parse_known_args();cfg=json.load(open(a.profile))
class H(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'loaded':True,'service':'strata'} if self.path=='/health' else {'data':[{'id':'fixture'}]}).encode())
HTTPServer(('127.0.0.1',a.port),H).serve_forever()
'''
        (self.control/'strata.py').write_text(script)
        foreign=ThreadingHTTPServer(('127.0.0.1',0),API);threading.Thread(target=foreign.serve_forever,daemon=True).start()
        self.addCleanup(foreign.server_close);self.addCleanup(foreign.shutdown)
        item=self.manager.status()['catalog'][0]
        with patch('strata_local.psutil.virtual_memory',return_value=SimpleNamespace(available=100*SL.GIB)):
            self.manager.start(item['id'])
        for _ in range(80):
            if self.manager.status()['state']=='running':break
            time.sleep(.05)
        self.assertEqual(self.manager.status()['state'],'running',self.manager.status())
        connection=self.manager.connection();self.assertTrue(connection['strata_owned']);self.assertEqual(connection['strata_package_id'],item['id'])
        cfg=json.loads((self.manager.root/'run.json').read_text())
        self.assertEqual(cfg['host'],'127.0.0.1');self.assertTrue(cfg['offline']);self.assertNotEqual(cfg['api_key'],'foreign-key')
        self.assertEqual((self.manager.root/'run.json').stat().st_mode & 0o777,0o600)
        self.assertNotIn(cfg['api_key'],json.dumps(self.manager.status()))
        self.manager.stop();self.assertFalse((self.manager.root/'run.json').exists())
        self.assertEqual(httpx.get(f'http://127.0.0.1:{foreign.server_port}/v1/models',headers={'Authorization':'Bearer fixture'}).status_code,200)


if __name__=='__main__':unittest.main()
