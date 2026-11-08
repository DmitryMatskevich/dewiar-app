"""Real WS authentication/wire/Bridge + current content Engine route, isolated host."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'core'),str(ROOT/'tools')]
from bridge import Bridge
from content_test_host import handler
from websockets.asyncio.server import serve
from websockets.asyncio.client import connect

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.bridge=Bridge('synthetic-token',port=0)
        self.bridge.loop=asyncio.get_running_loop()
        self.bridge.handler=handler(self.tmp.name)
        self.server=await serve(self.bridge._client,'127.0.0.1',0,compression=None,max_size=16*1024*1024)
        self.uri=f'ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}'

    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed();self.bridge.pool.shutdown(wait=True)

    async def test_auth_and_crud_wire(self):
        async with connect(self.uri) as ws:
            await ws.send(json.dumps({'cmd':'hello','token':'synthetic-token'}))
            self.assertEqual(json.loads(await ws.recv())['type'],'hello')
            async def call(i,cmd,args):
                await ws.send(json.dumps({'id':i,'cmd':cmd,'args':args}));return json.loads(await ws.recv())
            reply=await call(1,'content_create',{'title':'Через WS','body':'Текст','request_key':'wire'})
            self.assertTrue(reply['ok']);row=reply['data'];self.assertEqual(reply['id'],1)
            saved=await call(2,'content_update',{'id':row['id'],'expected_revision':1,'patch':{'title':'Изменено'}})
            self.assertEqual(saved['data']['revision'],2)
            stale=await call(3,'content_update',{'id':row['id'],'expected_revision':1,'patch':{'title':'Потеря'}})
            self.assertFalse(stale['ok']);self.assertIn('revision_conflict',stale['error'])
            self.assertEqual((await call(4,'content_get',{'id':row['id']}))['data']['title'],'Изменено')

    async def test_wrong_token_no_store(self):
        async with connect(self.uri) as ws:
            await ws.send(json.dumps({'cmd':'hello','token':'wrong'}))
            await ws.wait_closed();self.assertEqual(ws.close_code,4001)
        self.assertFalse((Path(self.tmp.name)/'Контент').exists())

    async def test_limited_connection_cannot_start_oauth_even_if_command_listed(self):
        self.bridge.allow('limited-token',{'content_oauth_create'})
        async with connect(self.uri) as ws:
            await ws.send(json.dumps({'cmd':'hello','token':'limited-token'}))
            self.assertTrue(json.loads(await ws.recv())['data']['limited'])
            await ws.send(json.dumps({'id':1,'cmd':'content_oauth_create','args':{'label':'Not trusted','request_key':'blocked','approved':True}}))
            reply=json.loads(await ws.recv());self.assertFalse(reply['ok']);self.assertIn('авторизованный',reply['error'])
        self.assertFalse((Path(self.tmp.name)/'Контент').exists())

if __name__=='__main__':unittest.main()
