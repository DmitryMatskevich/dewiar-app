"""Bounded read-only MCP discovery. No tool execution, token entry or API fallback."""
import hashlib
import json
import re
import threading
import time
import uuid
from urllib.parse import urlsplit
from .store import dump, now

ENDPOINT = 'https://mcp.higgsfield.ai/mcp'
METADATA = 'https://mcp.higgsfield.ai/.well-known/oauth-protected-resource/mcp'
PROTOCOL = '2025-11-25'
COMMANDS = {'content_higgsfield_diagnose', 'content_higgsfield_diagnostic'}


def migrate(c):
    c.execute('CREATE TABLE content_provider_diagnostics(provider TEXT PRIMARY KEY,run_id TEXT NOT NULL,state TEXT NOT NULL,deadline REAL NOT NULL,report TEXT NOT NULL)')


def _url(value, host=None):
    if not isinstance(value,str): raise ValueError('Некорректный адрес metadata')
    u=urlsplit(value)
    if u.scheme!='https' or u.username or u.password or u.query or u.fragment or u.port not in (None,443): raise ValueError('Metadata требует HTTPS без credentials и дополнительных параметров')
    if not u.hostname or not (u.hostname=='higgsfield.ai' or u.hostname.endswith('.higgsfield.ai')): raise ValueError('Сервер авторизации не относится к подтверждённому домену Higgsfield')
    if host and u.hostname!=host: raise ValueError('Смена домена metadata не разрешена')
    return value


def _request(client, method, url, **kw):
    deadline=time.monotonic()+15
    with client.stream(method,url,**kw) as r:
        data=bytearray()
        for chunk in r.iter_bytes():
            if time.monotonic()>deadline:raise ValueError('Истекло время чтения ответа')
            data.extend(chunk)
            if len(data)>2*1024*1024: raise ValueError('Ответ диагностики превышает 2 МиБ')
        return r.status_code,dict(r.headers),bytes(data)


def _json(data):
    value=json.loads(data)
    if not isinstance(value,dict): raise ValueError('Metadata должен быть объектом')
    return value


def _rpc(data,headers,ident):
    if 'event-stream' not in headers.get('content-type',''): result=_json(data)
    else:
        result=None
        for event in data.decode().replace('\r\n','\n').split('\n\n'):
            lines=[line[5:].lstrip() for line in event.splitlines() if line.startswith('data:')]
            if lines:
                candidate=_json('\n'.join(lines))
                if candidate.get('id')==ident: result=candidate;break
        if result is None:raise ValueError('Сервер не завершил ответ MCP')
    if result.get('id')!=ident or 'error' in result or not isinstance(result.get('result'),dict):raise ValueError('Неверный ответ MCP')
    return result['result']


def discover(client):
    headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
    status,response,data=_request(client,'POST',ENDPOINT,headers=headers,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':PROTOCOL,'capabilities':{},'clientInfo':{'name':'Dewiar-content-diagnostic','version':'1'}}})
    base={'provider':'higgsfield','endpoint':ENDPOINT,'checked_at':now(),'can_generate':False,
          'auth_connected':False,'api_fallback':False,'tools':[],'models_verified':False,'cost_verified':False}
    if status==401:
        challenge=response.get('www-authenticate','')
        match=re.search(r'resource_metadata="([^"]+)"',challenge)
        metadata=_url(match[1] if match else METADATA,host='mcp.higgsfield.ai')
        if not urlsplit(metadata).path.startswith('/.well-known/oauth-protected-resource'):raise ValueError('Неподдерживаемый адрес защищённого ресурса')
        code,_,raw=_request(client,'GET',metadata)
        if code!=200:raise ValueError('Не удалось прочитать metadata защищённого ресурса')
        protected=_json(raw)
        if protected.get('resource')!=ENDPOINT:raise ValueError('Metadata указывает другой MCP resource')
        servers=protected.get('authorization_servers')
        if not isinstance(servers,list) or len(servers)!=1:raise ValueError('Нужно явно поддержанное назначение OAuth issuer')
        issuer=_url(servers[0]); parsed=urlsplit(issuer)
        if parsed.path not in ('','/'):raise ValueError('Issuer с дополнительным путём пока не поддержан')
        code,_,raw=_request(client,'GET',issuer.rstrip('/')+'/.well-known/oauth-authorization-server')
        if code!=200:raise ValueError('Не удалось прочитать metadata сервера авторизации')
        oauth=_json(raw)
        if oauth.get('issuer')!=issuer:raise ValueError('OAuth issuer не совпадает')
        for field in ['authorization_endpoint','token_endpoint']:_url(oauth.get(field),host=parsed.hostname)
        if 'S256' not in oauth.get('code_challenge_methods_supported',[]) or 'code' not in oauth.get('response_types_supported',[]):raise ValueError('Сервер не подтвердил authorization code + PKCE S256')
        scopes=protected.get('scopes_supported',[])
        if not isinstance(scopes,list) or len(scopes)>50 or any(not isinstance(s,str) or len(s)>128 for s in scopes):raise ValueError('Некорректный список OAuth scopes')
        return {**base,'state':'auth_required','resource_metadata':metadata,'issuer':issuer,'scopes':scopes,'pkce':'S256',
                'message':'Сервер доступен. Требуется OAuth-вход. Эта диагностика не открывает вход и не выполняет генерацию.'}
    if status!=200:
        return {**base,'state':'unavailable','http_status':status,'message':'MCP недоступен для диагностики. Перенаправление или ошибка сервера не обходились.'}
    init=_rpc(data,response,1)
    negotiated=init.get('protocolVersion')
    if negotiated not in ('2025-11-25','2025-06-18','2025-03-26'):raise ValueError('Неподдерживаемая версия протокола MCP')
    headers['MCP-Protocol-Version']=negotiated
    session=response.get('mcp-session-id')
    if session:
        if len(session)>256 or any(ord(c)<32 for c in session):raise ValueError('Некорректный ID сессии')
        headers['Mcp-Session-Id']=session
    code,_,_=_request(client,'POST',ENDPOINT,headers=headers,json={'jsonrpc':'2.0','method':'notifications/initialized'})
    if code not in (200,202,204):raise ValueError('Сервер не принял инициализацию')
    tools,cursor,cursors=[],None,set()
    deadline=time.monotonic()+30
    for index in range(10):
        if time.monotonic()>deadline:raise ValueError('Истекло время чтения списка инструментов')
        ident=index+2
        code,h,raw=_request(client,'POST',ENDPOINT,headers=headers,json={'jsonrpc':'2.0','id':ident,'method':'tools/list','params':{'cursor':cursor} if cursor else {}})
        if code!=200:raise ValueError('Не удалось прочитать tools/list')
        result=_rpc(raw,h,ident);page=result.get('tools')
        if not isinstance(page,list) or len(page)>500 or len(tools)+len(page)>500:raise ValueError('Список инструментов превышает лимит')
        for tool in page:
            if not isinstance(tool,dict) or not isinstance(tool.get('name'),str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',tool['name']) or not isinstance(tool.get('inputSchema'),dict):raise ValueError('Некорректная схема инструмента')
            tools.append({'name':tool['name'],'schema_sha256':hashlib.sha256(dump(tool['inputSchema']).encode()).hexdigest()})
        cursor=result.get('nextCursor')
        if not cursor:break
        if not isinstance(cursor,str) or len(cursor)>4096 or cursor in cursors:raise ValueError('Неверный cursor списка инструментов')
        cursors.add(cursor)
    else:raise ValueError('Список инструментов не завершён за 10 страниц')
    if len({t['name'] for t in tools})!=len(tools):raise ValueError('Имена инструментов повторяются')
    return {**base,'state':'tools_discovered','protocol':negotiated,'tools':tools,
            'message':'Список инструментов прочитан без выполнения. Вход, модели, стоимость и возможность генерации ещё не проверены.'}


def get(store):
    with store.db() as c:
        c.execute("UPDATE content_provider_diagnostics SET state='interrupted' WHERE provider='higgsfield' AND state='running' AND deadline<?",(time.time(),))
        row=c.execute("SELECT * FROM content_provider_diagnostics WHERE provider='higgsfield'").fetchone()
        if not row:return {'state':'not_checked','endpoint':ENDPOINT,'can_generate':False,'message':'Подключение ещё не проверялось.'}
        result=json.loads(row['report']);result['state']=row['state'];result['run_id']=row['run_id']
        if row['state']=='interrupted':result['message']='Диагностика прервана или истекло время. Генерация не запускалась.'
        return result


def start(store, client_factory=None):
    run_id=uuid.uuid4().hex
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT run_id FROM content_provider_diagnostics WHERE provider='higgsfield' AND state='running' AND deadline>?",(time.time(),)).fetchone()
        if row:return {'run_id':row['run_id']}
        initial={'endpoint':ENDPOINT,'can_generate':False,'message':'Проверяются доступность MCP и параметры OAuth. Инструменты генерации не вызываются.'}
        c.execute('INSERT OR REPLACE INTO content_provider_diagnostics VALUES (?,?,?,?,?)',('higgsfield',run_id,'running',time.time()+150,dump(initial)))
    def work():
        try:
            import httpx
            factory=client_factory or (lambda:httpx.Client(timeout=httpx.Timeout(10,connect=5),follow_redirects=False))
            with factory() as client:report=discover(client)
        except Exception:
            # Do not persist untrusted response bodies/exception URLs or credentials.
            report={'state':'failed','endpoint':ENDPOINT,'can_generate':False,'checked_at':now(),'message':'Диагностика не завершилась: сеть или metadata не соответствует поддерживаемому контракту. Генерация не запускалась.'}
        with store.db() as c:
            c.execute("UPDATE content_provider_diagnostics SET state=?,report=? WHERE provider='higgsfield' AND run_id=? AND state='running' AND deadline>?",(report['state'],dump(report),run_id,time.time()))
    threading.Thread(target=work,daemon=True,name='content-higgsfield-diagnostic').start()
    return {'run_id':run_id}


def dispatch(store,cmd,args):
    if not isinstance(args,dict) or args:raise ValueError('Диагностика не принимает endpoint, токены или параметры генерации')
    return start(store) if cmd=='content_higgsfield_diagnose' else get(store)
