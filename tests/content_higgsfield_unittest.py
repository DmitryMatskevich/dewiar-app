"""Read-only discovery with fake HTTP; never uses account/model credentials."""
import json
from pathlib import Path
import sys
import unittest
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content import higgsfield as h


class HiggsfieldTests(unittest.TestCase):
    def fake(self, protected=None, oauth=None, redirect=False):
        calls=[]
        def route(request):
            calls.append((request.method,str(request.url),json.loads(request.content) if request.content else None))
            self.assertNotIn('authorization',request.headers)
            if request.method=='POST':
                self.assertEqual(json.loads(request.content)['method'],'initialize')
                if redirect:return httpx.Response(302,headers={'Location':'https://example.org/stolen'})
                return httpx.Response(401,headers={'WWW-Authenticate':'Bearer resource_metadata="'+h.METADATA+'"'},json={'error':'Unauthorized'})
            if str(request.url)==h.METADATA:return httpx.Response(200,json=protected or {'resource':h.ENDPOINT,'authorization_servers':['https://clerk.higgsfield.ai'],'scopes_supported':['openid','email','offline_access']})
            return httpx.Response(200,json=oauth or {'issuer':'https://clerk.higgsfield.ai','authorization_endpoint':'https://clerk.higgsfield.ai/oauth/authorize','token_endpoint':'https://clerk.higgsfield.ai/oauth/token','code_challenge_methods_supported':['S256'],'response_types_supported':['code']})
        return httpx.Client(transport=httpx.MockTransport(route),follow_redirects=False),calls

    def test_oauth_required_no_registration_no_generation(self):
        client,calls=self.fake()
        with client:result=h.discover(client)
        self.assertEqual(result['state'],'auth_required')
        self.assertFalse(result['can_generate']);self.assertFalse(result['api_fallback'])
        self.assertEqual(len(calls),3)
        self.assertNotIn('token_endpoint',result)

    def test_wrong_resource_or_issuer_rejected(self):
        for config in [{'protected':{'resource':'https://other.higgsfield.ai/mcp','authorization_servers':['https://clerk.higgsfield.ai']}},
                       {'protected':{'resource':h.ENDPOINT,'authorization_servers':['https://attacker.example']}},
                       {'oauth':{'issuer':'https://wrong.higgsfield.ai'}}]:
            client,_=self.fake(**config)
            with client,self.assertRaises(ValueError):h.discover(client)

    def test_redirect_not_followed(self):
        client,calls=self.fake(redirect=True)
        with client:result=h.discover(client)
        self.assertEqual(result['state'],'unavailable');self.assertEqual(len(calls),1)

    def test_no_pkce_or_foreign_token_host_rejected(self):
        for patch in [{'code_challenge_methods_supported':['plain']},{'token_endpoint':'https://other.higgsfield.ai/token'}]:
            config={'issuer':'https://clerk.higgsfield.ai','authorization_endpoint':'https://clerk.higgsfield.ai/oauth/authorize','token_endpoint':'https://clerk.higgsfield.ai/oauth/token','code_challenge_methods_supported':['S256'],'response_types_supported':['code'],**patch}
            client,_=self.fake(oauth=config)
            with client,self.assertRaises(ValueError):h.discover(client)

    def test_tools_discovery_sse_and_pagination_without_calls(self):
        methods=[]
        def route(request):
            data=json.loads(request.content);methods.append(data['method'])
            if data['method']=='initialize':return httpx.Response(200,json={'jsonrpc':'2.0','id':1,'result':{'protocolVersion':h.PROTOCOL}},headers={'mcp-session-id':'fake-session'})
            if data['method']=='notifications/initialized':return httpx.Response(202)
            self.assertEqual(request.headers['mcp-session-id'],'fake-session')
            result={'tools':[{'name':'fake_list_models','inputSchema':{'type':'object'}}],'nextCursor':'second'} if data['id']==2 else {'tools':[]}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=('event: message\ndata: '+json.dumps({'jsonrpc':'2.0','id':data['id'],'result':result})+'\n\n').encode())
        with httpx.Client(transport=httpx.MockTransport(route)) as client:result=h.discover(client)
        self.assertEqual(result['state'],'tools_discovered');self.assertEqual(len(result['tools']),1)
        self.assertNotIn('tools/call',methods);self.assertFalse(result['models_verified'])

    def test_ui_cannot_supply_token_or_other_endpoint(self):
        with self.assertRaises(ValueError):h.dispatch(None,'content_higgsfield_diagnose',{'token':'secret'})
        with self.assertRaises(ValueError):h.dispatch(None,'content_higgsfield_diagnose',{'endpoint':'http://127.0.0.1'})
        for value in ['http://mcp.higgsfield.ai/mcp','https://user:pass@mcp.higgsfield.ai/mcp','https://higgsfield.ai.attacker.example/mcp','https://higgsfield.ai/mcp?token=secret']:
            with self.assertRaises(ValueError):h._url(value)


if __name__=='__main__':unittest.main()
