"""Optional SDK runtime only. MockTransport, no sockets, files or credentials."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/content_mcp_runtime'))
import httpx2
from policy import Policy, RESOURCE, PRM, ISSUER, DISCOVERY, PROTOCOL, ContractError, discover
from transport import GuardedTransport


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = Policy(); self.calls = []; self.authorized = False; self.change = None
        self.oauth = {'issuer': ISSUER, 'authorization_endpoint': ISSUER+'/oauth/authorize',
                      'token_endpoint': ISSUER+'/oauth/token', 'registration_endpoint': ISSUER+'/oauth/register',
                      'code_challenge_methods_supported':['S256'], 'response_types_supported':['code']}
    async def route(self, request):
        self.calls.append((request.method, str(request.url)))
        if self.change:
            response = self.change(request)
            if response is not None: return response
        if str(request.url) == PRM: return httpx2.Response(200, json={'resource':RESOURCE,'authorization_servers':[ISSUER],'scopes_supported':['openid']})
        if str(request.url) in DISCOVERY: return httpx2.Response(200, json=self.oauth)
        if str(request.url) == RESOURCE:
            if not self.authorized: return httpx2.Response(401, headers={'www-authenticate':f'Bearer resource_metadata="{PRM}"'})
            value=json.loads(request.content)
            if value['method']=='notifications/initialized':return httpx2.Response(202)
            result={'protocolVersion':PROTOCOL} if value['method']=='initialize' else {'tools':[{'name':'test_image','inputSchema':{'type':'object'}}]}
            return httpx2.Response(200,json={'jsonrpc':'2.0','id':value['id'],'result':result})
        raise AssertionError('Unexpected network request')
    def client(self):
        return httpx2.AsyncClient(transport=GuardedTransport(self.policy,httpx2.MockTransport(self.route)),follow_redirects=False,trust_env=False)

    async def test_discovery_and_bounded_public_result(self):
        async with self.client() as client: result=await discover(client,self.policy)
        self.assertEqual(result['state'],'auth_required');self.assertFalse(result['can_generate'])
        self.assertEqual(self.calls,[('POST',RESOURCE),('GET',PRM),('GET',DISCOVERY[0])])

    async def test_tool_list_has_hash_without_execution(self):
        self.authorized=True
        async with self.client() as client: result=await discover(client,self.policy)
        self.assertEqual(result['tools'][0]['name'],'test_image');self.assertEqual(len(result['tools'][0]['schema_sha256']),64)
        self.assertEqual(len(self.calls),3)

    async def test_untrusted_url_and_tools_call_never_reach_network(self):
        async with self.client() as client:
            for url in ['http://127.0.0.1/private','https://mcp.higgsfield.ai/other','https://else.higgsfield.ai/mcp',ISSUER+'/oauth/token']:
                with self.assertRaises(ContractError):await client.post(url,content=b'PRIVATE')
            with self.assertRaises(ContractError):await client.post(RESOURCE,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'paid'}})
        self.assertEqual(self.calls,[])

    async def test_metadata_credentials_and_unverified_issuer_not_sent(self):
        async with self.client() as client:
            with self.assertRaises(ContractError):await client.get(PRM,headers={'Authorization':'Bearer FAKE'})
            with self.assertRaises(ContractError):await client.get(DISCOVERY[0])
        self.assertEqual(self.calls,[])

    async def test_bad_resource_issuer_endpoint_or_pkce_rejected(self):
        for change in [{'issuer':'https://wrong.example'}, {'token_endpoint':'https://wrong.example/token'}, {'token_endpoint':ISSUER+'/token?secret=x'}, {'code_challenge_methods_supported':['plain']}]:
            self.policy=Policy();self.oauth.update(change)
            async with self.client() as client:
                with self.assertRaises(ContractError):await discover(client,self.policy)
            self.setUp()
        self.change=lambda r: httpx2.Response(200,json={'resource':RESOURCE+'/wrong','authorization_servers':[ISSUER]}) if str(r.url)==PRM else None
        async with self.client() as client:
            with self.assertRaises(ContractError):await discover(client,self.policy)

    async def test_redirect_not_followed_even_if_client_enabled_it(self):
        self.change=lambda r:httpx2.Response(307,headers={'location':'https://wrong.example/SECRET'})
        async with self.client() as client:
            client.follow_redirects=True
            with self.assertRaises(ContractError):await discover(client,self.policy)
        self.assertEqual(self.calls,[('POST',RESOURCE)])

    async def test_body_size_and_content_length_limits(self):
        for headers,content in [({},b'x'*(2*1024*1024+1)),({'content-length':'999999999'},b'x')]:
            self.change=lambda r:httpx2.Response(200,headers=headers,content=content)
            async with self.client() as client:
                with self.assertRaises(ContractError):await discover(client,self.policy)

    async def test_oidc_fallback_and_foreign_challenge(self):
        self.change=lambda r:httpx2.Response(404) if str(r.url)==DISCOVERY[0] else None
        async with self.client() as client:self.assertEqual((await discover(client,self.policy))['state'],'auth_required')
        self.assertIn(('GET',DISCOVERY[1]),self.calls)
        self.setUp();self.change=lambda r:httpx2.Response(401,headers={'www-authenticate':'Bearer resource_metadata="https://wrong.example/meta"'})
        async with self.client() as client:
            with self.assertRaises(ContractError):await discover(client,self.policy)
        self.assertEqual(len(self.calls),1)

    async def test_incomplete_sse_and_duplicate_tool_names_rejected(self):
        self.authorized=True
        self.change=lambda r:httpx2.Response(200,headers={'content-type':'text/event-stream'},content=b'data: {"jsonrpc":"2.0","id":999,"result":{}}\n\n')
        async with self.client() as client:
            with self.assertRaises(ContractError):await discover(client,self.policy)
        self.change=lambda r:httpx2.Response(200,json={'jsonrpc':'2.0','id':2,'result':{'tools':[{'name':'x','inputSchema':{}},{'name':'x','inputSchema':{}}]}}) if json.loads(r.content)['method']=='tools/list' else None
        async with self.client() as client:
            with self.assertRaises(ContractError):await discover(client,self.policy)


if __name__ == '__main__':unittest.main()
