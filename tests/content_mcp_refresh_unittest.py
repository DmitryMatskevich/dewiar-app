"""Restart/refresh contracts in optional SDK runtime; only a fake provider."""
import copy
import time
import unittest
from urllib.parse import parse_qs
import httpx2
from content_mcp_callback_unittest import LoginTests
from oauth import refresh
from policy import RESOURCE,ISSUER,DISCOVERY,PROTOCOL,ContractError


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=LoginTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.bundle=(await self.fixture.login())['credential'];self.calls=[];self.rotate=True;self.fail=False;self.wrong_issuer=False
    async def route(self,r):
        url=str(r.url);self.calls.append((r.method,url))
        if url in DISCOVERY and self.wrong_issuer:
            response=await self.fixture.route(r);data=response.json();data['issuer']='https://wrong.example'
            return httpx2.Response(200,json=data)
        if url==ISSUER+'/oauth/token':
            p=parse_qs(r.content.decode());self.assertEqual(p['grant_type'],['refresh_token']);self.assertEqual(p['resource'],[RESOURCE])
            self.assertEqual(p['refresh_token'],['FAKE-refresh'])
            if self.fail:return httpx2.Response(400,json={'error':'invalid_grant','error_description':'FAKE-private-error'})
            return httpx2.Response(200,json={'access_token':'FAKE-new','token_type':'Bearer','expires_in':3600,**({'refresh_token':'FAKE-rotated'} if self.rotate else {})})
        if url==RESOURCE and r.headers.get('authorization')=='Bearer FAKE-new':
            import json
            v=json.loads(r.content)
            if v['method']=='notifications/initialized':return httpx2.Response(202)
            return httpx2.Response(200,json={'jsonrpc':'2.0','id':v['id'],'result':{'protocolVersion':PROTOCOL} if v['method']=='initialize' else {'tools':[]}})
        return await self.fixture.route(r)
    async def refresh(self):return await refresh(self.bundle,httpx2.MockTransport(self.route))

    async def test_expired_absolute_time_forces_rotation_after_restart(self):
        self.bundle['expires_at']=time.time()-1;self.bundle['tokens']['expires_in']=999999
        result=await self.refresh();value=result['credential']
        self.assertEqual(value['tokens']['refresh_token'],'FAKE-rotated');self.assertGreater(value['expires_at'],time.time())
        token_index=self.calls.index(('POST',ISSUER+'/oauth/token'))
        self.assertGreater(token_index,self.calls.index(('GET',DISCOVERY[0])))
        self.assertLess(token_index,self.calls.index(('POST',RESOURCE)))

    async def test_missing_expiry_forces_refresh_and_omission_preserves_scope(self):
        self.bundle['expires_at']=None;self.rotate=False
        result=await self.refresh();value=result['credential']
        self.assertEqual(value['tokens']['refresh_token'],'FAKE-refresh')
        self.assertEqual(value['tokens']['scope'],self.bundle['tokens']['scope'])

    async def test_valid_expiry_is_not_extended_on_restart(self):
        expected=self.bundle['expires_at'];result=await self.refresh()
        self.assertEqual(result['credential']['expires_at'],expected)
        self.assertNotIn(('POST',ISSUER+'/oauth/token'),self.calls)

    async def test_fresh_wrong_issuer_blocks_before_any_token_request(self):
        self.bundle['expires_at']=1;self.wrong_issuer=True
        with self.assertRaises(ContractError):await self.refresh()
        self.assertNotIn(('POST',ISSUER+'/oauth/token'),self.calls);self.assertNotIn(('POST',RESOURCE),self.calls)

    async def test_persisted_metadata_never_supplies_fallback_token_endpoint(self):
        self.bundle['expires_at']=1;self.bundle['oauth_metadata']['token_endpoint']='http://127.0.0.1/private'
        await self.refresh()
        self.assertIn(('POST',ISSUER+'/oauth/token'),self.calls)
        self.assertTrue(all(not url.startswith('http://') for _,url in self.calls))

    async def test_failure_or_missing_refresh_does_not_register_or_open_login(self):
        self.bundle['expires_at']=1;self.fail=True
        with self.assertRaises(ContractError):await self.refresh()
        self.assertNotIn(('POST',ISSUER+'/oauth/register'),self.calls)
        self.calls=[];self.fail=False;self.bundle['tokens']['refresh_token']=None
        with self.assertRaises(ContractError):await self.refresh()
        self.assertNotIn(('POST',ISSUER+'/oauth/register'),self.calls);self.assertNotIn(('POST',ISSUER+'/oauth/token'),self.calls)


if __name__=='__main__':unittest.main(defaultTest='RefreshTests')
