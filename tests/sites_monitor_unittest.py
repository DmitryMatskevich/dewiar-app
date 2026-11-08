"""Isolated monitor tests; no application Engine, real mail or public network."""
import json
import io
from datetime import date
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
from sites.store import Store, availability, validate
from sites.probes import execute, specs
from sites.runtime import Runtime
from sites.server import App, Handler, ThreadingHTTPServer
import urllib.request
import urllib.error


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        with patch('sites.store.minute',return_value=100):
            self.site=self.store.save({'name':'Fixture','url':'https://example.org','period':5})
    def result(self,state,at,uid=None):
        return self.store.ingest(self.site,[dict(probe='http',state=state,reason='fixture')],uid or str(at),at)
    def test_stable_compaction(self):
        for t in range(100,200,5):self.result('UP',t)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM spans WHERE probe='web'").fetchone()[0],1)
        h=self.store.history(self.site,100,200);self.assertEqual((h['U'],h['D'],h['X']),(100,0,0))
    def test_metadata_preserves_incident(self):
        self.result('DOWN',100);self.result('DOWN',101)
        with patch('sites.store.minute',return_value=102):self.store.save(dict(self.site,hoster='Provider'))
        self.assertIsNone(self.store.incidents()[0]['closed'])
    def test_recipient_snapshot_and_coalesce(self):
        self.store.save_settings({'channels':['simulation'], 'email_to':'before@example.org'})
        self.result('DOWN',100);self.result('DOWN',101)
        self.store.save_settings({'email_to':'after@example.org'})
        with self.store.db() as c:
            self.assertEqual(json.loads(c.execute('SELECT target FROM outbox').fetchone()[0])['email_to'],'before@example.org')
        self.result('UP',106);self.result('UP',107)
        self.assertEqual(sorted(r['state'] for r in self.store.deliveries()),['pending','superseded'])
    def test_renewal_reminders_threshold_once_and_expiry(self):
        self.site=self.store.save(dict(self.site,hosting_expires='2026-10-15'))
        self.store.save_settings({'channels':['simulation']})
        self.assertEqual(self.store.queue_renewal_reminders(date(2026,10,1)),1)
        with self.store.db() as c:
            first=c.execute('SELECT body FROM outbox').fetchone()['body']
        self.assertIn('14 дн.',first)
        self.assertEqual(self.store.queue_renewal_reminders(date(2026,10,1)),0)
        self.assertEqual(self.store.queue_renewal_reminders(date(2026,10,15)),1)
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM renewal_alerts').fetchone()[0],2)
        with self.store.db() as c:self.assertIn('Сегодня заканчивается',c.execute('SELECT body FROM outbox ORDER BY rowid DESC LIMIT 1').fetchone()['body'])
    def test_renewal_date_validation(self):
        with self.assertRaises(ValueError):self.store.save(dict(self.site,hosting_expires='31.12.2026'))
    def test_unknown_gap(self):
        self.result('UP',100);self.result('UP',115)
        h=self.store.history(self.site,100,120);self.assertEqual((h['U'],h['X']),(10,10))
    def test_short_failure_remains_no_incident(self):
        self.result('DOWN',100);self.result('UP',101)
        h=self.store.history(self.site,100,105);self.assertEqual(h['D'],1);self.assertFalse(self.store.incidents())
    def test_down_up_deduplicated(self):
        self.store.save_settings({'channels':['simulation']})
        self.result('DOWN',100);self.result('DOWN',101);self.result('DOWN',106)
        self.result('UP',111);self.result('UP',112)
        self.assertEqual(len(self.store.incidents()),1) # One web incident, no duplicate HTTP incident.
        self.assertEqual(len(self.store.deliveries()),2)
        self.assertFalse(self.result('UP',112))
    def test_stale_revision(self):
        with patch('sites.store.minute',return_value=101):new=self.store.save(dict(self.site,name='Changed'))
        self.assertFalse(self.result('DOWN',102));self.assertEqual(new['rev'],2)
        with self.assertRaises(ValueError):self.store.save(dict(self.site,name='Stale'))
    def test_unknown_breaks_confirmation(self):
        self.result('DOWN',100);self.result('UNKNOWN',101);self.result('DOWN',102)
        self.assertFalse(self.store.incidents())
    def test_pause_excluded(self):
        self.result('UP',100)
        with patch('sites.store.minute',return_value=102):self.store.save_settings({'paused':True})
        h=self.store.history(self.site,100,110);self.assertEqual(h['E'],8)
    def test_resume_resets_confirmation(self):
        self.result('DOWN',100)
        with patch('sites.store.minute',return_value=100):self.store.save_settings({'paused':True})
        with patch('sites.store.minute',return_value=101):self.store.save_settings({'paused':False})
        self.result('DOWN',101)
        self.assertFalse(self.store.incidents())
    def test_empty_history(self):
        h=self.store.history(self.site,100,110);self.assertIsNone(h['uptime']);self.assertEqual(h['X'],10)
    def test_validation(self):
        for data in [{'url':'file:///etc/passwd'},{'url':'https://name:pass@example.org'},{'url':'https://example.org','period':0}]:
            with self.assertRaises(ValueError):validate(data)
    def test_aggregate_overlap(self):
        spans=[dict(probe=p,start=0,end=10,state='DOWN') for p in ['a','b','c']]
        h=availability(spans,[],['a','b','c'],0,10);self.assertEqual(h['D'],10)


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path=='/redirect':self.send_response(302);self.send_header('Location','http://169.254.169.254/');self.end_headers();return
                self.send_response(503 if self.path=='/down' else 200);self.end_headers();self.wfile.write(b'fixture healthy')
            def log_message(self,*a):pass
        cls.http=HTTPServer(('127.0.0.1',0),Fixture)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.thread.join()
    def probe(self,path='',marker='',test=True):return execute(dict(id='http',kind='http',target=self.url+path,marker=marker),test)
    def test_http(self):self.assertEqual(self.probe()['state'],'UP')
    def test_down(self):self.assertEqual(self.probe('/down')['reason'],'HTTP_STATUS')
    def test_marker(self):self.assertEqual(self.probe(marker='missing')['reason'],'CONTENT_MISMATCH')
    def test_ssrf(self):self.assertEqual(self.probe(test=False)['reason'],'TARGET_POLICY')
    def test_redirect(self):self.assertEqual(self.probe('/redirect')['reason'],'TARGET_POLICY')
    def test_https_default_port_host_header(self):
        # Some production servers redirect Host: example.org:443 back to the same
        # URL. Exercise the real HTTP serializer without a TLS/network fixture.
        class Socket:
            request = b''
            def sendall(self, data):self.request += data
            def makefile(self, *args):return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')
            def getpeercert(self):return {}
            def close(self):pass
        sock=Socket()
        with patch('sites.probes.connect',return_value=(sock,'93.184.215.14')):
            result=execute(dict(id='http',kind='http',target='https://example.org/'))
        self.assertEqual(result['state'],'UP')
        self.assertIn(b'Host: example.org\r\n',sock.request)
        self.assertNotIn(b'Host: example.org:443',sock.request)
    def test_independent_services_and_dns_host(self):
        site=validate({'name':'Fixture','url':'https://site.example','dns_host':'dns.example','console_url':'https://site.example/operator','health_url':'https://site.example/health','check_http':True,'check_https':False,'ssh':True,'ftp':True})
        checks=specs(site)
        by_id={r['id']:r for r in checks}
        self.assertEqual(by_id['dns']['target'],'dns.example')
        self.assertEqual(by_id['http_port']['target'],'http://site.example/')
        self.assertNotIn('https_port',by_id)
        self.assertEqual(by_id['console']['target'],'https://site.example/operator')
        self.assertIn('health',by_id)
    def test_dns_host_validation(self):
        with self.assertRaises(ValueError):validate({'url':'https://site.example','dns_host':'bad/name'})


class RuntimeTests(unittest.TestCase):
    def test_network_policy_reconciles_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root)
            site=store.save({'name':'Fixture','url':'https://example.org'})
            with store.db() as c:store._exclude(c,site['id'],'network_policy',True,100)
            config=Path(root)/'settings.json'
            config.write_text('{"privacy":{"local_only":false}}')
            runtime=Runtime(store,config_path=config)
            try:
                with patch.object(runtime.stop,'wait',side_effect=[False,True]),patch.object(runtime,'submit'):
                    runtime.loop()
                with store.db() as c:
                    self.assertIsNotNone(c.execute('SELECT end FROM exclusions').fetchone()[0])
            finally:runtime.pool.shutdown()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=App(Path(self.tmp.name)/'state',Path(self.tmp.name)/'config',test=True)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.app.runtime.pool.shutdown()
    def request(self,path,body=None,auth=True,origin=None):
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+self.app.token
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body else None,headers=headers)
        with urllib.request.urlopen(req) as r:return json.load(r)
    def test_auth(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/sites',auth=False)
        self.assertEqual(e.exception.code,401)
    def test_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/sites',origin='https://evil.example')
        self.assertEqual(e.exception.code,401)
    def test_crud(self):
        s=self.request('/api/sites',dict(name='A',url='https://example.org'))['item']
        self.request('/api/sites',dict(s,name='B'))
        self.assertEqual(self.request('/api/sites')['items'][0]['name'],'B')
    def test_poll_while_writing(self):
        for i in range(15):
            self.request('/api/sites',dict(name=str(i),url='https://example.org'))
            self.assertIn('database_bytes',self.request('/api/status'))
    def test_secret_reference_only(self):
        from vault import Vault
        self.app.config.mkdir()
        v=Vault(self.app.config/'vault.bin',self.app.config/'vault.key')
        item=v.put('Synthetic panel','FAKE-SECRET-FOR-TEST',login='test',url='https://panel.example.org')
        self.request('/api/sites',dict(name='A',url='https://example.org',vault_id=item['id']))
        self.assertNotIn('FAKE-SECRET',json.dumps(self.request('/api/access')))
        self.assertNotIn('FAKE-SECRET',json.dumps(self.request('/api/sites')))
        self.assertNotIn(b'FAKE-SECRET',self.app.store.path.read_bytes())
        self.assertEqual(self.request('/api/reveal',{'id':item['id']})['value'],'FAKE-SECRET-FOR-TEST')
    def test_phone_token_read_only(self):
        headers={'Authorization':'Bearer '+self.app.reader_token}
        with urllib.request.urlopen(urllib.request.Request(self.url+'/api/mobile/summary',headers=headers)) as r:
            self.assertEqual(json.load(r)['schema_version'],1)
        for path in ('/api/access','/api/sites'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(self.url+path,headers=headers))
            self.assertEqual(error.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(self.url+'/api/settings',data=b'{"paused":true}',headers=headers))
    def test_status(self):self.assertTrue(self.request('/api/status')['test'])
    def test_discover_is_explicit_public_lookup_and_does_not_save_site(self):
        result={'domain':'example.org','addresses':['93.184.216.34'],'nameservers':['ns.example.org'],'registrar':'Example','domain_expires':'2030-01-01','networks':[],'sources':[],'note':'test'}
        with patch('sites.server.discovery.discover',return_value=result) as lookup:
            self.assertEqual(self.request('/api/discover',{'domain':'example.org'}),result)
            lookup.assert_called_once_with('example.org')
        self.assertEqual(self.request('/api/sites')['items'],[])


if __name__=='__main__':unittest.main()
