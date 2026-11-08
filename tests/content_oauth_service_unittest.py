"""Trusted-interface boundary and private worker lifecycle, all isolated."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'core'),str(ROOT/'tools')]
from content.store import ContentStore
from content import oauth_store
from content.oauth_service import Service,command
from content_oauth_fixture import OAuthFixture
from interface_origin import authenticated_interface
from content_test_host import websocket,handler
from websockets.sync.client import connect


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.store=ContentStore(root/'workspace'/'Контент');self.fixture=OAuthFixture();self.fixture.base_url='http://127.0.0.1:12345'
        self.service=Service(root/'config',root/'config'/'key',runtime=sys.executable,runner=self.fixture)
        self.host=SimpleNamespace(_content_oauth_service=self.service)
        self.addCleanup(self.service.close)
    def call(self,cmd,args):
        with authenticated_interface():return command(self.host,self.store,cmd,args)
    def start(self):
        self.service.check_runtime();row=oauth_store.create(self.store,'ТЕСТ','create')
        args={'id':row['id'],'expected_revision':1,'operation':'authorize','request_key':'login'}
        self.service.start(self.store,args)
        for _ in range(100):
            value=self.service.get(self.store,row['id'])
            if value['authorization_url']:return value,args
            time.sleep(.01)
        self.fail('Worker did not present fake login URL')
    def done(self,ident):
        for _ in range(100):
            value=self.service.get(self.store,ident)
            if value['state'] not in oauth_store.BUSY:return value
            time.sleep(.01)
        self.fail('Worker did not finish')

    def test_untrusted_command_rejected_even_with_approved_argument(self):
        with self.assertRaisesRegex(ValueError,'авторизованный'):command(self.host,self.store,'content_oauth_create',{'label':'x','request_key':'x','approved':True})
        with self.assertRaisesRegex(ValueError,'параметры'):self.call('content_oauth_create',{'label':'x','request_key':'x','approved':True})
        self.assertEqual(oauth_store.listing(self.store),[])

    def test_login_approve_refresh_and_disconnect_keep_secrets_private(self):
        row,args=self.start();uri=row['authorization_url'];ident=uri.rsplit('/',1)[1]
        self.fixture.decide(ident,'approve');connected=self.done(row['id'])
        self.assertEqual(connected['state'],'connected');self.assertIsNone(connected['authorization_url'])
        self.assertNotIn('TEST-access',json.dumps(connected));self.assertNotIn('credential_ref',connected)
        self.assertFalse(connected['account_identity_verified']);self.assertFalse(connected['can_generate'])
        self.service.start(self.store,{'id':row['id'],'expected_revision':connected['revision'],'operation':'refresh','request_key':'refresh'})
        refreshed=self.done(row['id']);self.assertEqual(refreshed['credential_generation'],2)
        disconnected=self.service.disconnect(self.store,row['id'],refreshed['revision'])
        self.assertEqual(disconnected['state'],'disconnected');self.assertFalse(disconnected['remote_revoked'])
        self.assertEqual(list(self.service.vault.root.glob('*.bin')),[])

    def test_same_key_does_not_spawn_another_helper_and_cancel_fences_result(self):
        row,args=self.start();again=self.service.start(self.store,args)
        self.assertEqual(again['intent_id'],row['intent_id']);self.assertEqual(len(self.fixture.pending),1)
        value=self.service.cancel(self.store,row['id'],row['intent_id'])
        self.assertEqual(value['last_error'],'cancelled');self.assertIsNone(value['authorization_url'])
        self.assertEqual(self.fixture.pending,{});self.assertEqual(self.service.active,{})
        self.assertFalse(self.service.vault.root.exists())

    def test_close_cancels_outstanding_login(self):
        row,_=self.start();self.service.close()
        self.assertEqual(self.service.active,{});self.assertEqual(self.fixture.pending,{})
        self.assertEqual(self.service.get(self.store,row['id'])['last_error'],'cancelled')

    def test_rejection_keeps_disconnected(self):
        row,_=self.start();self.fixture.decide(row['authorization_url'].rsplit('/',1)[1],'deny')
        result=self.done(row['id']);self.assertEqual(result['state'],'disconnected');self.assertIsNone(result['authorization_url'])
        self.assertFalse(self.service.vault.root.exists())

    def test_workspace_isolation_and_real_bridge_origin(self):
        direct=handler(Path(self.tmp.name)/'untrusted',oauth_runner=self.fixture)
        with self.assertRaisesRegex(ValueError,'авторизованный'):direct('content_oauth_create',{'label':'x','request_key':'x'})
        self.assertFalse((Path(self.tmp.name)/'untrusted'/'Контент').exists())
        with websocket(Path(self.tmp.name)/'trusted',oauth_runner=self.fixture) as (port,token):
            with connect(f'ws://127.0.0.1:{port}') as ws:
                ws.send(json.dumps({'cmd':'hello','token':token}));self.assertEqual(json.loads(ws.recv())['type'],'hello')
                ws.send(json.dumps({'id':1,'cmd':'content_oauth_create','args':{'label':'ТЕСТ — WS','request_key':'ws'}}))
                reply=json.loads(ws.recv());self.assertTrue(reply['ok']);self.assertEqual(reply['data']['label'],'ТЕСТ — WS')
        self.assertEqual(oauth_store.listing(self.store),[])


if __name__=='__main__':unittest.main()
