"""Offline OAuth lifecycle with real loopback sockets and a fake issuer."""
import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/content_mcp_runtime'))
import httpx2
from callback import Callback, CallbackDenied
from oauth import authorize
from policy import RESOURCE, PRM, ISSUER, DISCOVERY, PROTOCOL


async def callback_request(uri,query,host=None,path=None):
    u=urlsplit(uri);reader,writer=await asyncio.open_connection('127.0.0.1',u.port)
    target=(path or u.path)+'?'+urlencode(query)
    writer.write(f'GET {target} HTTP/1.1\r\nHost: {host or u.netloc}\r\n\r\n'.encode());await writer.drain()
    response=await reader.read(16384);writer.close();await writer.wait_closed();return response


class CallbackTests(unittest.IsolatedAsyncioTestCase):
    def url(self,listener):
        return ISSUER+'/oauth/authorize?'+urlencode({'response_type':'code','client_id':'FAKE','redirect_uri':listener.redirect_uri,
            'resource':RESOURCE,'state':'a'*43,'code_challenge':'b'*43,'code_challenge_method':'S256'})
    async def test_invalid_state_host_path_and_issuer_do_not_consume_session(self):
        async with Callback() as listener:
            listener.arm(self.url(listener),ISSUER+'/oauth/authorize',True)
            good={'code':'FAKE-code','state':'a'*43,'iss':ISSUER}
            for query,host,path in [({**good,'state':'wrong'},None,None),({**good,'iss':'https://wrong.example'},None,None),(good,'evil.example',None),(good,None,'/wrong'),({'code':'FAKE','state':'a'*43},None,None)]:
                raw=await callback_request(listener.redirect_uri,query,host,path)
                self.assertIn(b'400 Bad Request',raw);self.assertNotIn(b'FAKE-code',raw);self.assertFalse(listener.future.done())
            raw=await callback_request(listener.redirect_uri,good)
            self.assertIn(b'200 OK',raw);self.assertIn(b'no-store',raw)
            self.assertEqual((await listener.wait())['code'],'FAKE-code')
            await listener.server.wait_closed()
            with self.assertRaises(OSError):await asyncio.open_connection('127.0.0.1',urlsplit(listener.redirect_uri).port)

    async def test_timeout_closes_port_and_error_callback_has_no_reflection(self):
        async with Callback() as listener:
            port=urlsplit(listener.redirect_uri).port
            listener.arm(self.url(listener),ISSUER+'/oauth/authorize',False)
            with self.assertRaises(TimeoutError):await listener.wait(.01)
        with self.assertRaises(OSError):await asyncio.open_connection('127.0.0.1',port)
        async with Callback() as listener:
            listener.arm(self.url(listener),ISSUER+'/oauth/authorize',False)
            raw=await callback_request(listener.redirect_uri,{'state':'a'*43,'error':'access_denied','error_description':'SECRET-description'})
            self.assertNotIn(b'SECRET',raw)
            with self.assertRaises(CallbackDenied):await listener.wait()

    async def test_duplicate_state_parameter_and_code_error_rejected(self):
        async with Callback() as listener:
            listener.arm(self.url(listener),ISSUER+'/oauth/authorize',False)
            for params in [[('code','x'),('state','a'*43),('state','a'*43)],{'state':'a'*43,'code':'x','error':'access_denied'}]:
                self.assertIn(b'400 Bad Request',await callback_request(listener.redirect_uri,params))
                self.assertFalse(listener.future.done())


class LoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        old=logging.root.manager.disable;logging.disable(logging.CRITICAL);self.addCleanup(logging.disable,old)
        self.calls=[];self.redirect=None;self.params=None;self.registered=None
        self.token_redirect=False;self.deny=False;self.secret=False;self.bad_callback=False
    async def route(self,r):
        url=str(r.url);self.calls.append((r.method,url))
        if url==RESOURCE:
            if r.headers.get('authorization')!='Bearer FAKE-access':return httpx2.Response(401,headers={'www-authenticate':f'Bearer resource_metadata="{PRM}"'})
            v=json.loads(r.content)
            if v['method']=='notifications/initialized':return httpx2.Response(202)
            result={'protocolVersion':PROTOCOL} if v['method']=='initialize' else {'tools':[{'name':'render_fake','inputSchema':{'type':'object'}}]}
            return httpx2.Response(200,json={'jsonrpc':'2.0','id':v['id'],'result':result})
        if url==PRM:return httpx2.Response(200,json={'resource':RESOURCE,'authorization_servers':[ISSUER],'scopes_supported':['openid','offline_access']})
        if url in DISCOVERY:return httpx2.Response(200,json={'issuer':ISSUER,'authorization_endpoint':ISSUER+'/oauth/authorize','token_endpoint':ISSUER+'/oauth/token','registration_endpoint':ISSUER+'/oauth/register','response_types_supported':['code'],'grant_types_supported':['authorization_code','refresh_token'],'code_challenge_methods_supported':['S256'],'token_endpoint_auth_methods_supported':['none'],'authorization_response_iss_parameter_supported':True})
        if url==ISSUER+'/oauth/register':
            self.registered=json.loads(r.content);self.redirect=self.registered['redirect_uris'][0]
            return httpx2.Response(201,json={**self.registered,'client_id':'FAKE-client',**({'client_secret':'unexpected'} if self.secret else {})})
        if url==ISSUER+'/oauth/token':
            self.assertEqual(parse_qs(r.content.decode())['resource'],[RESOURCE])
            data=parse_qs(r.content.decode());self.assertEqual(data['redirect_uri'],[self.redirect])
            proof=base64.urlsafe_b64encode(hashlib.sha256(data['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
            self.assertEqual(self.params['code_challenge'],[proof]);self.assertEqual(data['code'],['FAKE-code'])
            if self.token_redirect:return httpx2.Response(307,headers={'location':'https://wrong.example/token'})
            return httpx2.Response(200,json={'access_token':'FAKE-access','refresh_token':'FAKE-refresh','token_type':'Bearer','expires_in':3600})
        raise AssertionError('Unexpected endpoint')
    async def on_url(self,url):
        self.params=parse_qs(urlsplit(url).query)
        query={'state':self.params['state'][0],'iss':ISSUER}
        if self.deny:query['error']='access_denied'
        else:query['code']='FAKE-code'
        if self.bad_callback:query['iss']='https://wrong.example'
        await callback_request(self.redirect,query)
    async def login(self):return await authorize(self.on_url,httpx2.MockTransport(self.route),callback_timeout=.2)

    async def test_complete_pkce_callback_and_tools_only(self):
        result=await self.login()
        self.assertEqual(result['report']['state'],'tools_discovered');self.assertFalse(result['report']['can_generate'])
        bundle=result['credential'];self.assertEqual(bundle['tokens']['access_token'],'FAKE-access')
        self.assertEqual(bundle['client_info']['issuer'],ISSUER);self.assertGreater(bundle['expires_at'],0)
        self.assertNotIn('id_token',bundle['tokens'])
        self.assertEqual(self.registered['token_endpoint_auth_method'],'none')

    async def test_denial_and_wrong_callback_never_exchange_tokens(self):
        self.deny=True
        with self.assertRaises(CallbackDenied):await self.login()
        self.assertNotIn(('POST',ISSUER+'/oauth/token'),self.calls)
        self.setUp();self.bad_callback=True
        with self.assertRaises(TimeoutError):await self.login()
        self.assertNotIn(('POST',ISSUER+'/oauth/token'),self.calls)

    async def test_redirect_and_client_secret_rejected(self):
        self.token_redirect=True
        with self.assertRaises(Exception):await self.login()
        self.assertTrue(all('wrong.example' not in url for _,url in self.calls))
        self.setUp();self.secret=True
        with self.assertRaises(Exception):await self.login()
        self.assertNotIn(('POST',ISSUER+'/oauth/token'),self.calls)

    async def test_cancel_closes_listener_before_token_exchange(self):
        started=asyncio.Event()
        async def pending(url):started.set()
        task=asyncio.create_task(authorize(pending,httpx2.MockTransport(self.route)))
        async def cleanup():
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        await asyncio.wait_for(started.wait(),2);port=urlsplit(self.redirect).port
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        with self.assertRaises(OSError):await asyncio.open_connection('127.0.0.1',port)
        self.assertNotIn(('POST',ISSUER+'/oauth/token'),self.calls)


if __name__=='__main__':unittest.main()
