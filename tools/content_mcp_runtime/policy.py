"""Higgsfield profile v1. No user supplied URLs or tool execution.

This package is loaded only by the optional SDK process, never via core/mcp.py.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs

RESOURCE = 'https://mcp.higgsfield.ai/mcp'
PRM = 'https://mcp.higgsfield.ai/.well-known/oauth-protected-resource/mcp'
ISSUER = 'https://clerk.higgsfield.ai'
DISCOVERY = (ISSUER + '/.well-known/oauth-authorization-server',
             ISSUER + '/.well-known/openid-configuration')
PROTOCOL = '2025-11-25'
MAX_BODY = 2 * 1024 * 1024
MAX_METADATA = 128 * 1024
MAX_REQUEST = 64 * 1024


class ContractError(Exception):
    """Deliberately no response bodies, URLs, OAuth values or SDK exceptions."""


def decode(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: raise ContractError()
            result[k] = v
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError()))
    except (ValueError, UnicodeError, RecursionError): raise ContractError() from None
    if not isinstance(value, dict): raise ContractError()
    return value


def issuer_endpoint(value):
    if not isinstance(value, str) or len(value) > 2048: raise ContractError()
    u = urlsplit(value)
    if (u.scheme != 'https' or u.netloc != 'clerk.higgsfield.ai' or not u.path.startswith('/')
            or u.username or u.password or u.query or u.fragment or '\\' in value
            or any(ord(c) < 33 for c in value)): raise ContractError()
    return value


class Policy:
    def __init__(self, redirect_uri=None, refresh=False):
        self.protected = self.oauth = None
        self.redirect_uri = redirect_uri
        self.refresh = refresh

    def request(self, method, url, headers, body):
        if len(body) > MAX_REQUEST: raise ContractError()
        if url == RESOURCE:
            if method != 'POST': raise ContractError()
            value = decode(body)
            if set(value) - {'jsonrpc', 'id', 'method', 'params'} or value.get('jsonrpc') != '2.0': raise ContractError()
            verb = value.get('method')
            if verb not in ('initialize', 'notifications/initialized', 'tools/list'): raise ContractError()
            if verb == 'notifications/initialized':
                if 'id' in value or value.get('params', {}) != {}: raise ContractError()
            elif type(value.get('id')) is not int: raise ContractError()
            if verb == 'tools/list':
                params = value.get('params', {})
                if not isinstance(params, dict) or set(params) - {'cursor'}: raise ContractError()
                if 'cursor' in params and (not isinstance(params['cursor'], str) or len(params['cursor']) > 4096): raise ContractError()
            return MAX_BODY
        if (self.redirect_uri or self.refresh) and self.oauth and url in (self.oauth.get('registration_endpoint'),self.oauth['token_endpoint']):
            if method!='POST' or any(name in headers for name in ('authorization','cookie','proxy-authorization')):raise ContractError()
            if url==self.oauth.get('registration_endpoint'):
                if self.refresh:raise ContractError()
                value={k:v for k,v in decode(body).items() if v is not None}
                if set(value)-{'redirect_uris','token_endpoint_auth_method','grant_types','response_types','client_name','scope','application_type'} or value.get('application_type','native')!='native':raise ContractError()
                if value.get('redirect_uris')!=[self.redirect_uri] or value.get('token_endpoint_auth_method')!='none' or value.get('grant_types')!=['authorization_code','refresh_token'] or value.get('response_types',['code'])!=['code']:raise ContractError()
            else:
                try:params=parse_qs(body.decode(),strict_parsing=True,max_num_fields=8)
                except (ValueError,UnicodeError):raise ContractError() from None
                if any(len(v)!=1 for v in params.values()):raise ContractError()
                p={k:v[0] for k,v in params.items()}
                if self.refresh:
                    if set(p)!={'grant_type','refresh_token','client_id','resource'} or p['grant_type']!='refresh_token' or p['resource']!=RESOURCE:raise ContractError()
                else:
                    if set(p)!={'grant_type','code','code_verifier','redirect_uri','client_id','resource'} or p['resource']!=RESOURCE or p['redirect_uri']!=self.redirect_uri or p['grant_type']!='authorization_code':raise ContractError()
                    if not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',p['code_verifier']):raise ContractError()
            return MAX_METADATA
        if url not in (PRM, *DISCOVERY) or method != 'GET' or body: raise ContractError()
        if any(name in headers for name in ('authorization', 'cookie', 'proxy-authorization')): raise ContractError()
        if url in DISCOVERY and self.protected is None: raise ContractError()
        return MAX_METADATA

    def response(self, url, status, body):
        if 300 <= status < 400: raise ContractError()
        if url == PRM and status == 200:
            data = decode(body)
            if data.get('resource') != RESOURCE or data.get('authorization_servers') != [ISSUER]: raise ContractError()
            scopes = data.get('scopes_supported', [])
            if not isinstance(scopes, list) or len(scopes) > 50 or any(not isinstance(s, str) or not re.fullmatch(r'[!-~]{1,128}', s) for s in scopes): raise ContractError()
            self.protected = data
        elif url in DISCOVERY and status == 200:
            data = decode(body)
            if data.get('issuer') != ISSUER: raise ContractError()
            for name in ('authorization_endpoint', 'token_endpoint'): issuer_endpoint(data.get(name))
            if data.get('registration_endpoint'): issuer_endpoint(data['registration_endpoint'])
            endpoints=[data.get(k) for k in ('authorization_endpoint','token_endpoint','registration_endpoint') if data.get(k)]
            if len(endpoints)!=len(set(endpoints)) or any(e in DISCOVERY for e in endpoints):raise ContractError()
            if 'S256' not in data.get('code_challenge_methods_supported', []) or 'code' not in data.get('response_types_supported', []): raise ContractError()
            self.oauth = data


def rpc_result(response, ident):
    if response.status_code != 200: raise ContractError()
    if 'text/event-stream' in response.headers.get('content-type', ''):
        result = None
        try: text = response.content.decode('utf-8')
        except UnicodeError: raise ContractError() from None
        for event in text.replace('\r\n', '\n').split('\n\n'):
            lines = [line[5:].lstrip() for line in event.splitlines() if line.startswith('data:')]
            if not lines: continue
            candidate = decode('\n'.join(lines))
            if candidate.get('id') == ident:
                if result is not None: raise ContractError()
                result = candidate
    else: result = decode(response.content)
    if not result or result.get('jsonrpc') != '2.0' or result.get('id') != ident or 'error' in result or not isinstance(result.get('result'), dict): raise ContractError()
    return result['result']


async def discover(client, policy):
    headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'Dewiar-content', 'version': '1'}}}
    response = await client.post(RESOURCE, headers=headers, json=payload)
    if response.status_code == 401:
        challenge = response.headers.get('www-authenticate', '')
        match = re.search(r'resource_metadata="([^"]+)"', challenge)
        if match and match[1] != PRM: raise ContractError()
        if (await client.get(PRM)).status_code != 200: raise ContractError()
        for url in DISCOVERY:
            response = await client.get(url)
            if response.status_code == 200: break
            if response.status_code not in (404, 405): raise ContractError()
        if policy.oauth is None: raise ContractError()
        return {'state': 'auth_required', 'resource': RESOURCE, 'issuer': ISSUER,
                'scopes': policy.protected.get('scopes_supported', []), 'pkce': 'S256', 'can_generate': False}
    result = rpc_result(response, 1)
    protocol = result.get('protocolVersion')
    if protocol not in (PROTOCOL, '2025-06-18', '2025-03-26'): raise ContractError()
    headers['MCP-Protocol-Version'] = protocol
    session = response.headers.get('mcp-session-id')
    if session:
        if len(session) > 256 or not re.fullmatch(r'[!-~]+', session): raise ContractError()
        headers['Mcp-Session-Id'] = session
    response = await client.post(RESOURCE, headers=headers, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    if response.status_code not in (200, 202, 204): raise ContractError()
    tools, cursors, cursor = [], set(), None
    for ident in range(2, 12):
        response = await client.post(RESOURCE, headers=headers, json={'jsonrpc': '2.0', 'id': ident, 'method': 'tools/list', 'params': {'cursor': cursor} if cursor else {}})
        result = rpc_result(response, ident)
        page = result.get('tools')
        if not isinstance(page, list) or len(tools) + len(page) > 500: raise ContractError()
        for tool in page:
            if not isinstance(tool, dict) or not isinstance(tool.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', tool['name']) or not isinstance(tool.get('inputSchema'), dict): raise ContractError()
            digest = hashlib.sha256(json.dumps(tool['inputSchema'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            tools.append({'name': tool['name'], 'schema_sha256': digest})
        cursor = result.get('nextCursor')
        if cursor is None: break
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors: raise ContractError()
        cursors.add(cursor)
    else: raise ContractError()
    if len({t['name'] for t in tools}) != len(tools): raise ContractError()
    return {'state': 'tools_discovered', 'resource': RESOURCE, 'protocol': protocol, 'tools': tools, 'can_generate': False}
