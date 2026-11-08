"""Single-use loopback OAuth callback. No URL/query logging or reflected data."""
import asyncio
import secrets
from urllib.parse import parse_qs, urlsplit
from policy import ContractError, ISSUER, RESOURCE


def auth_parameters(url, redirect_uri, endpoint):
    if not isinstance(url, str) or len(url) > 8192: raise ContractError()
    u = urlsplit(url)
    if u.fragment or u.scheme+'://'+u.netloc+u.path != endpoint: raise ContractError()
    try: params = parse_qs(u.query, strict_parsing=True, max_num_fields=12)
    except ValueError: raise ContractError() from None
    required = {'response_type','client_id','redirect_uri','state','code_challenge','code_challenge_method','resource'}
    if not required <= set(params) or set(params)-required-{'scope','prompt'} or any(len(v)!=1 for v in params.values()): raise ContractError()
    p = {k:v[0] for k,v in params.items()}
    if p['response_type']!='code' or p['redirect_uri']!=redirect_uri or p['resource']!=RESOURCE or p['code_challenge_method']!='S256': raise ContractError()
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',p['state']) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',p['code_challenge']): raise ContractError()
    if not p['client_id'] or len(p['client_id'])>2048 or any(any(ord(c)<32 for c in v) for v in p.values()): raise ContractError()
    if p.get('prompt') not in (None,'consent'): raise ContractError()
    return p


class CallbackDenied(Exception): pass


class Callback:
    def __init__(self):
        self.server=None;self.state=None;self.require_issuer=False
        self.path='/oauth/'+secrets.token_urlsafe(24)
        self.connections=set();self.tasks=set();self.closed=False

    async def __aenter__(self):
        self.future=asyncio.get_running_loop().create_future()
        self.server=await asyncio.start_server(self.handle,'127.0.0.1',0,limit=8192,backlog=8)
        self.host='127.0.0.1:'+str(self.server.sockets[0].getsockname()[1])
        self.redirect_uri='http://'+self.host+self.path
        return self

    def arm(self, url, endpoint, require_issuer):
        if self.state is not None or self.closed: raise ContractError()
        self.state=auth_parameters(url,self.redirect_uri,endpoint)['state']
        self.require_issuer=bool(require_issuer)

    async def handle(self,reader,writer):
        task=asyncio.current_task();self.tasks.add(task)
        accepted=False
        try:
            if self.closed or len(self.connections)>=8:return
            self.connections.add(writer)
            raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),3)
            if len(raw)>8192:raise ValueError()
            lines=raw.decode('ascii').split('\r\n');method,target,version=lines[0].split(' ')
            headers={}
            for line in lines[1:]:
                if not line:break
                key,value=line.split(':',1);key=key.lower()
                if key in headers:raise ValueError()
                headers[key]=value.strip()
            if method!='GET' or version not in ('HTTP/1.0','HTTP/1.1') or headers.get('host')!=self.host or headers.get('content-length','0')!='0' or 'transfer-encoding' in headers:raise ValueError()
            url=urlsplit(target)
            if url.scheme or url.netloc or url.path!=self.path or url.fragment or not self.state or self.future.done():raise ValueError()
            params=parse_qs(url.query,strict_parsing=True,keep_blank_values=True,max_num_fields=8)
            if set(params)-{'code','state','iss','error','error_description','error_uri'} or any(len(v)!=1 for v in params.values()):raise ValueError()
            p={k:v[0] for k,v in params.items()}
            if not secrets.compare_digest(p.get('state',''),self.state):raise ValueError()
            if p.get('iss') not in (None,ISSUER) or (self.require_issuer and p.get('iss')!=ISSUER):raise ValueError()
            if 'error' in p:
                if 'code' in p or not p['error']:raise ValueError()
                self.future.set_exception(CallbackDenied())
            else:
                code=p.get('code','')
                if not code or len(code)>4096 or any(ord(c)<33 for c in code):raise ValueError()
                self.future.set_result({'code':code,'state':self.state,'iss':p.get('iss')})
            accepted=True
            self.server.close()
        except (ValueError,UnicodeError,TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):pass
        finally:
            text='Ответ получен. Вернитесь в Dewiar.' if accepted else 'Ответ не принят. Вернитесь в Dewiar и проверьте состояние входа.'
            body=('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Dewiar · вход</title><p>'+text+'</p></html>').encode()
            status=b'200 OK' if accepted else b'400 Bad Request'
            try:
                writer.write(b'HTTP/1.1 '+status+b'\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src \'none\'\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                await asyncio.wait_for(writer.drain(),1)
            except (ConnectionError,OSError,TimeoutError):pass
            writer.close();self.connections.discard(writer);self.tasks.discard(task)

    async def wait(self,timeout=300):
        return await asyncio.wait_for(asyncio.shield(self.future),timeout)

    async def __aexit__(self,*_):
        self.closed=True
        if self.server:self.server.close();await self.server.wait_closed()
        for writer in list(self.connections):writer.close()
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if not self.future.done():self.future.cancel()
        elif not self.future.cancelled():self.future.exception()  # retrieve any abandoned denial
