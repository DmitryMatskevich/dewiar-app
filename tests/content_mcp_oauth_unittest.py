"""Offline contract for the optional, isolated official MCP runtime.

Run with its Python, NOT app/.venv: mcp==2.3.0 uses httpx2 and core/mcp.py
would shadow the SDK if core were put on sys.path. No network, credentials,
browser, device key or user settings are accessed here.
"""
import asyncio
import base64
import hashlib
import logging
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.exceptions import OAuthFlowError, OAuthTokenError
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

RESOURCE = "https://mcp.higgsfield.ai/mcp"
ISSUER = "https://clerk.higgsfield.ai"
PRM = "https://mcp.higgsfield.ai/.well-known/oauth-protected-resource/mcp"
REDIRECT = "http://127.0.0.1:41861/oauth/callback"


class MemoryStorage:
    def __init__(self):
        self.tokens = self.client = None
        self.writes = []

    async def get_tokens(self): return self.tokens
    async def get_client_info(self): return self.client
    async def set_client_info(self, value): self.client = value
    async def set_tokens(self, value):
        self.tokens = value
        self.writes.append(value.model_copy(deep=True))


class OAuthContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # SDK error logging can contain a token response body or OAuth state.
        # The production subprocess must suppress it, not forward stderr.
        self.logging_level = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, self.logging_level)
        self.storage = MemoryStorage()
        self.calls = []
        self.auth_params = None
        self.challenge = None
        self.wrong_state = self.wrong_iss = False
        self.prm_resource = RESOURCE
        self.metadata_issuer = ISSUER
        self.redirect_token = False
        self.refreshes = 0
        self.rotate = True

    async def redirect(self, url):
        self.assertEqual(urlsplit(url).scheme + '://' + urlsplit(url).netloc, ISSUER)
        self.auth_params = parse_qs(urlsplit(url).query)
        self.assertEqual(self.auth_params['redirect_uri'], [REDIRECT])
        self.assertEqual(self.auth_params['resource'], [RESOURCE])
        self.assertEqual(self.auth_params['code_challenge_method'], ['S256'])
        self.assertEqual(self.auth_params['prompt'], ['consent'])
        self.challenge = self.auth_params['code_challenge'][0]

    async def callback(self):
        return AuthorizationCodeResult(code='FAKE-code',
            state='wrong-state' if self.wrong_state else self.auth_params['state'][0],
            iss='https://wrong.example' if self.wrong_iss else ISSUER)

    async def resource_guard(self, server, advertised):
        if server != RESOURCE or advertised != RESOURCE:
            raise ValueError('Resource mismatch')

    async def route(self, request):
        url = str(request.url)
        self.calls.append((request.method, url))
        if url == RESOURCE:
            if request.headers.get('authorization') in ('Bearer FAKE-access', 'Bearer FAKE-refreshed'):
                return httpx2.Response(200, json={'ok': True})
            return httpx2.Response(401, headers={'www-authenticate': f'Bearer resource_metadata="{PRM}"'})
        if url == PRM:
            return httpx2.Response(200, json={'resource': self.prm_resource,
                'authorization_servers': [ISSUER], 'scopes_supported': ['openid', 'email', 'offline_access']})
        if url == ISSUER + '/.well-known/oauth-authorization-server':
            return httpx2.Response(200, json={'issuer': self.metadata_issuer,
                'authorization_endpoint': ISSUER + '/oauth/authorize', 'token_endpoint': ISSUER + '/oauth/token',
                'registration_endpoint': ISSUER + '/oauth/register', 'response_types_supported': ['code'],
                'grant_types_supported': ['authorization_code', 'refresh_token'],
                'code_challenge_methods_supported': ['S256'], 'token_endpoint_auth_methods_supported': ['none'],
                'authorization_response_iss_parameter_supported': True})
        if url == ISSUER + '/oauth/register':
            import json
            metadata = json.loads(request.content)
            self.assertEqual(metadata['redirect_uris'], [REDIRECT])
            self.assertEqual(metadata['token_endpoint_auth_method'], 'none')
            return httpx2.Response(201, json={**metadata, 'client_id': 'FAKE-client'})
        if url == ISSUER + '/oauth/token':
            if self.redirect_token:
                return httpx2.Response(307, headers={'location': 'https://wrong.example/token'})
            data = parse_qs(request.content.decode())
            self.assertEqual(data['resource'], [RESOURCE])
            if data['grant_type'] == ['authorization_code']:
                verifier = data['code_verifier'][0]
                computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                self.assertEqual(computed, self.challenge)
                self.assertEqual(data['code'], ['FAKE-code'])
                return httpx2.Response(200, json={'access_token': 'FAKE-access', 'token_type': 'Bearer',
                    'refresh_token': 'FAKE-refresh', 'expires_in': 3600})
            self.assertEqual(data['grant_type'], ['refresh_token'])
            self.assertEqual(data['refresh_token'], ['FAKE-refresh'])
            self.refreshes += 1
            await asyncio.sleep(0)
            value = {'access_token': 'FAKE-refreshed', 'token_type': 'Bearer', 'expires_in': 3600}
            if self.rotate: value['refresh_token'] = 'FAKE-rotated'
            return httpx2.Response(200, json=value)
        raise AssertionError('Unexpected request endpoint')

    def client(self):
        self.auth = OAuthClientProvider(server_url=RESOURCE, storage=self.storage,
            client_metadata=OAuthClientMetadata(redirect_uris=[REDIRECT], client_name='Dewiar offline contract',
                token_endpoint_auth_method='none', grant_types=['authorization_code', 'refresh_token']),
            redirect_handler=self.redirect, callback_handler=self.callback, validate_resource_url=self.resource_guard)
        return httpx2.AsyncClient(auth=self.auth, transport=httpx2.MockTransport(self.route),
            follow_redirects=False, headers={'MCP-Protocol-Version': '2025-11-25'})

    async def test_pkce_resource_state_and_persistence(self):
        async with self.client() as client:
            response = await client.post(RESOURCE, json={'offline_contract': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.storage.client.issuer, ISSUER)
        self.assertEqual(set(self.storage.tokens.scope.split()), {'openid', 'email', 'offline_access'})
        self.assertEqual(len(self.storage.writes), 1)

    async def test_wrong_state_prevents_token_exchange(self):
        self.wrong_state = True
        async with self.client() as client:
            with self.assertRaises(OAuthFlowError):
                await client.post(RESOURCE)
        self.assertNotIn(('POST', ISSUER + '/oauth/token'), self.calls)
        self.assertIsNone(self.storage.tokens)

    async def test_wrong_callback_issuer_prevents_token_exchange(self):
        self.wrong_iss = True
        async with self.client() as client:
            with self.assertRaises(OAuthFlowError):
                await client.post(RESOURCE)
        self.assertNotIn(('POST', ISSUER + '/oauth/token'), self.calls)

    async def test_wrong_metadata_issuer_prevents_registration(self):
        self.metadata_issuer = 'https://wrong.example'
        async with self.client() as client:
            with self.assertRaises(OAuthFlowError):
                await client.post(RESOURCE)
        self.assertNotIn(('POST', ISSUER + '/oauth/register'), self.calls)

    async def test_wrong_resource_prevents_registration(self):
        self.prm_resource = 'https://mcp.higgsfield.ai/other'
        async with self.client() as client:
            with self.assertRaises(ValueError):
                await client.post(RESOURCE)
        self.assertNotIn(('POST', ISSUER + '/oauth/register'), self.calls)

    async def test_token_redirect_not_followed(self):
        self.redirect_token = True
        async with self.client() as client:
            with self.assertRaises(OAuthTokenError):
                await client.post(RESOURCE)
        self.assertTrue(all('wrong.example' not in url for _, url in self.calls))
        self.assertIsNone(self.storage.tokens)

    async def test_concurrent_refresh_once_and_rotation(self):
        async with self.client() as client:
            await client.post(RESOURCE)
            self.auth.context.token_expiry_time = 1
            responses = await asyncio.gather(*(client.post(RESOURCE) for _ in range(3)))
        self.assertEqual([r.status_code for r in responses], [200] * 3)
        self.assertEqual(self.refreshes, 1)
        self.assertEqual(self.storage.tokens.refresh_token, 'FAKE-rotated')
        self.assertEqual(self.storage.writes[0].refresh_token, 'FAKE-refresh')

    async def test_refresh_omission_preserves_refresh_and_scope(self):
        self.rotate = False
        async with self.client() as client:
            await client.post(RESOURCE)
            old_scope = self.storage.tokens.scope
            self.auth.context.token_expiry_time = 1
            await client.post(RESOURCE)
        self.assertEqual(self.refreshes, 1)
        self.assertEqual(self.storage.tokens.refresh_token, 'FAKE-refresh')
        self.assertEqual(self.storage.tokens.scope, old_scope)


if __name__ == '__main__': unittest.main()
