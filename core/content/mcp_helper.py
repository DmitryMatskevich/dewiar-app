"""Private, bounded subprocess bridge. No secret/SDK output reaches app logs.

Runtime path is a host configuration, never a command supplied by a model/UI.
No installation, fallback to the main Python, shell, or tools/call here.
"""
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

VERSION = 1
MAX_LINE = 256 * 1024
ENTRY = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2])) / 'tools/content_mcp_runtime/main.py'
ERRORS = {'runtime_unavailable', 'operation_failed', 'authorization_denied', 'timeout'}


@dataclass(repr=False)
class CredentialBundle:
    value: dict = field(repr=False)
    def __repr__(self): return '<CredentialBundle redacted>'


@dataclass(repr=False)
class LoginResult:
    report: dict
    credential: CredentialBundle = field(repr=False)
    def __repr__(self): return '<LoginResult redacted>'


class HelperError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _result(value, operation):
    if operation in ('authorize','refresh'):
        if not isinstance(value,dict) or set(value)!={'report','credential'}:raise HelperError('protocol_error')
        report=_result(value['report'],'discover')
        if report['state']!='tools_discovered':raise HelperError('protocol_error')
        credential=value['credential']
        if not isinstance(credential,dict) or set(credential)!={'format','resource','issuer','client_info','tokens','expires_at','oauth_metadata','protected_metadata'}:raise HelperError('protocol_error')
        if type(credential['format']) is not int or credential['format']!=1 or credential['resource']!='https://mcp.higgsfield.ai/mcp' or credential['issuer']!='https://clerk.higgsfield.ai':raise HelperError('protocol_error')
        token=credential['tokens'];info=credential['client_info'];expiry=credential['expires_at']
        if not isinstance(token,dict) or set(token)!={'access_token','refresh_token','token_type','expires_in','scope'}:raise HelperError('protocol_error')
        for name in ('access_token','refresh_token','token_type','scope'):
            if token[name] is not None and (not isinstance(token[name],str) or len(token[name])>16384 or any(ord(c)<32 for c in token[name])):raise HelperError('protocol_error')
        if not token['access_token'] or not isinstance(token['token_type'],str) or token['token_type'].lower()!='bearer':raise HelperError('protocol_error')
        if expiry is not None and (type(expiry) not in (float,int) or not math.isfinite(expiry) or expiry<=0):raise HelperError('protocol_error')
        if token['expires_in'] is not None and (type(token['expires_in']) is not int or token['expires_in']<=0):raise HelperError('protocol_error')
        if not isinstance(info,dict) or info.get('issuer')!=credential['issuer'] or info.get('client_secret') or not isinstance(info.get('client_id'),str) or not info['client_id'] or len(info['client_id'])>2048:raise HelperError('protocol_error')
        if not isinstance(credential['oauth_metadata'],dict) or credential['oauth_metadata'].get('issuer')!=credential['issuer'] or not isinstance(credential['protected_metadata'],dict) or credential['protected_metadata'].get('resource')!=credential['resource'] or credential['protected_metadata'].get('authorization_servers')!=[credential['issuer']]:raise HelperError('protocol_error')
        return LoginResult(report,CredentialBundle(credential))
    if not isinstance(value, dict) or value.get('can_generate') is not False: raise HelperError('protocol_error')
    state = value.get('state')
    if operation == 'health':
        if value != {'state': 'ready', 'sdk': '2.3.0', 'protocol': VERSION, 'can_generate': False}: raise HelperError('protocol_error')
    elif state == 'auth_required':
        if set(value) != {'state', 'resource', 'issuer', 'scopes', 'pkce', 'can_generate'} or value['resource'] != 'https://mcp.higgsfield.ai/mcp' or value['issuer'] != 'https://clerk.higgsfield.ai' or value['pkce'] != 'S256': raise HelperError('protocol_error')
        if not isinstance(value['scopes'], list) or len(value['scopes']) > 50 or any(not isinstance(s, str) or not re.fullmatch(r'[!-~]{1,128}', s) for s in value['scopes']): raise HelperError('protocol_error')
    elif state == 'tools_discovered':
        if set(value) != {'state', 'resource', 'protocol', 'tools', 'can_generate'} or value['resource'] != 'https://mcp.higgsfield.ai/mcp' or value['protocol'] not in ('2025-11-25', '2025-06-18', '2025-03-26'): raise HelperError('protocol_error')
        if not isinstance(value['tools'], list) or len(value['tools']) > 500: raise HelperError('protocol_error')
        for tool in value['tools']:
            if not isinstance(tool, dict) or set(tool) != {'name', 'schema_sha256'} or not isinstance(tool['name'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', tool['name']) or not isinstance(tool['schema_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', tool['schema_sha256']): raise HelperError('protocol_error')
        if len({t['name'] for t in value['tools']}) != len(value['tools']): raise HelperError('protocol_error')
    else: raise HelperError('protocol_error')
    return value


def _authorization_url(value):
    if not isinstance(value,str) or len(value)>8192:raise HelperError('protocol_error')
    u=urlsplit(value)
    if u.scheme!='https' or u.netloc!='clerk.higgsfield.ai' or u.fragment or not u.path.startswith('/'):raise HelperError('protocol_error')
    try:params=parse_qs(u.query,strict_parsing=True,max_num_fields=12)
    except ValueError:raise HelperError('protocol_error') from None
    required={'response_type','client_id','redirect_uri','state','code_challenge','code_challenge_method','resource'}
    if not required<=set(params) or set(params)-required-{'scope','prompt'} or any(len(v)!=1 for v in params.values()):raise HelperError('protocol_error')
    p={k:v[0] for k,v in params.items()}
    if p['resource']!='https://mcp.higgsfield.ai/mcp' or p['response_type']!='code' or p['code_challenge_method']!='S256' or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',p['state']) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',p['code_challenge']):raise HelperError('protocol_error')
    redirect=urlsplit(p['redirect_uri'])
    try:port=redirect.port
    except ValueError:raise HelperError('protocol_error') from None
    if redirect.scheme!='http' or redirect.hostname!='127.0.0.1' or redirect.username or redirect.password or not port or redirect.query or redirect.fragment or not re.fullmatch(r'/oauth/[A-Za-z0-9_-]{32}',redirect.path):raise HelperError('protocol_error')
    return value


def call(python, operation, *, cancel=None, timeout=50, on_authorization_url=None, credential=None, _entry=ENTRY):
    if operation not in ('health', 'discover', 'authorize', 'refresh'): raise HelperError('unsupported_operation')
    if not 0 < timeout <= (360 if operation=='authorize' else 60): raise HelperError('invalid_timeout')
    if operation=='authorize' and not callable(on_authorization_url):raise HelperError('missing_login_handler')
    if operation=='refresh':
        if not isinstance(credential,CredentialBundle):raise HelperError('invalid_credential')
        _result({'report':{'state':'tools_discovered','resource':'https://mcp.higgsfield.ai/mcp','protocol':'2025-11-25','tools':[],'can_generate':False},'credential':credential.value},'refresh')
    elif credential is not None:raise HelperError('invalid_credential')
    # Preserve the venv executable symlink, which selects its pyvenv.cfg.
    executable = Path(python).expanduser()
    if not executable.is_absolute() or not executable.is_file(): raise HelperError('runtime_unavailable')
    if cancel is not None and cancel.is_set(): raise HelperError('cancelled')
    ident = uuid.uuid4().hex
    frame = json.dumps({'v': VERSION, 'id': ident, 'operation': operation,**({'credential':credential.value} if credential else {})},ensure_ascii=True,allow_nan=False).encode() + b'\n'
    if len(frame)>MAX_LINE:raise HelperError('invalid_credential')
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TMPDIR', 'TMP', 'TEMP') if k in os.environ}
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        process = subprocess.Popen([str(executable), '-I', '-B', str(_entry)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=str(_entry.parent),
            creationflags=0x08000000 if os.name == 'nt' else 0)
    except OSError: raise HelperError('runtime_unavailable') from None
    results = queue.Queue(maxsize=3)
    def read():
        try:
            def pairs(items):
                result = {}
                for k, v in items:
                    if k in result: raise ValueError()
                    result[k] = v
                return result
            for _ in range(3):
                raw = process.stdout.readline(MAX_LINE + 1)
                if raw==b'':results.put_nowait(('eof',None));return
                if len(raw) > MAX_LINE or not raw.endswith(b'\n'): raise ValueError()
                value = json.loads(raw, object_pairs_hook=pairs)
                results.put_nowait(('frame',value))
            raise ValueError()
        except Exception:
            try:results.put_nowait(('invalid',None))
            except queue.Full:pass
    reader = threading.Thread(target=read, daemon=True, name='content-mcp-ipc')
    reader.start()
    def write():
        try:process.stdin.write(frame);process.stdin.close()
        except (OSError,ValueError):pass
    writer=threading.Thread(target=write,daemon=True,name='content-mcp-input');writer.start()
    deadline = time.monotonic() + timeout
    try:
        reply=None;event_seen=False
        while True:
            if cancel is not None and cancel.is_set(): raise HelperError('cancelled')
            left = deadline - time.monotonic()
            if left <= 0: raise HelperError('timeout')
            try: kind,response = results.get(timeout=min(.05, left))
            except queue.Empty: continue
            if kind=='eof':
                if reply is None:raise HelperError('protocol_error')
                response=reply;break
            if kind=='invalid' or not isinstance(response,dict) or response.get('v')!=VERSION or type(response.get('v')) is not int or response.get('id')!=ident or reply is not None:raise HelperError('protocol_error')
            if 'event' in response:
                if operation!='authorize' or event_seen or set(response)!={'v','id','event','url'} or response['event']!='authorization_url':raise HelperError('protocol_error')
                url=_authorization_url(response['url']);event_seen=True
                try:on_authorization_url(url)
                except Exception:raise HelperError('operation_failed') from None
            else:reply=response
        if type(response.get('ok')) is not bool: raise HelperError('protocol_error')
        if response['ok']:
            if set(response) != {'v', 'id', 'ok', 'result'}: raise HelperError('protocol_error')
            if operation=='authorize' and not event_seen:raise HelperError('protocol_error')
            if cancel is not None and cancel.is_set():raise HelperError('cancelled')
            return _result(response['result'], operation)
        if set(response) != {'v', 'id', 'ok', 'error'} or not isinstance(response['error'],str) or response['error'] not in ERRORS: raise HelperError('protocol_error')
        raise HelperError(response['error'])
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
        reader.join(2)
        writer.join(2)
        for stream in (process.stdin, process.stdout):
            if stream and not stream.closed: stream.close()
