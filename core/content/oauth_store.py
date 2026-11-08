"""OAuth connection ledger; credentials and browser state never enter SQLite."""
import hashlib
import json
import time
import uuid
from .store import dump,integer,now
from .mcp_helper import LoginResult,HelperError,_result

BUSY={'authenticating','verifying'}


def migrate(c):
    c.execute('CREATE TABLE content_oauth_connections(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,label TEXT NOT NULL,archived INTEGER NOT NULL,provider TEXT NOT NULL,state TEXT NOT NULL,credential_ref TEXT,credential_generation INTEGER NOT NULL,metadata TEXT NOT NULL,intent_id TEXT,operation TEXT,deadline REAL,updated_at TEXT NOT NULL,last_error TEXT)')
    c.execute('CREATE TABLE content_oauth_events(id TEXT PRIMARY KEY,connection_id TEXT NOT NULL REFERENCES content_oauth_connections(id),generation INTEGER NOT NULL,event TEXT NOT NULL,created_at TEXT NOT NULL)')


def _get(c,ident):
    if not isinstance(ident,str):raise ValueError('Укажите подключение')
    row=c.execute('SELECT * FROM content_oauth_connections WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('Подключение не найдено')
    return dict(row)


def public(row):
    return {**{k:row[k] for k in ('id','revision','label','provider','state','credential_generation','deadline','updated_at','last_error')},
            'archived':bool(row['archived']),'metadata':json.loads(row['metadata']),
            'account_identity_verified':False,'can_generate':False,'remote_revoked':False}


def _event(c,row,event):
    c.execute('INSERT INTO content_oauth_events VALUES (?,?,?,?,?)',(uuid.uuid4().hex,row['id'],row['credential_generation'],event,now()))


def binding(store,row,generation=None):
    return {'workspace':hashlib.sha256(str(store.path.resolve()).encode()).hexdigest(),'connection':row['id'],
            'generation':row['credential_generation'] if generation is None else generation}


def recover(c):
    for record in c.execute("SELECT * FROM content_oauth_connections WHERE state IN ('authenticating','verifying') AND deadline<?",(time.time(),)).fetchall():
        row=dict(record)
        c.execute("UPDATE content_oauth_connections SET state='needs_login',revision=revision+1,intent_id=NULL,operation=NULL,deadline=NULL,last_error='interrupted',updated_at=? WHERE id=?",(now(),row['id']))
        _event(c,row,'interrupted')


def get(store,ident):
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');recover(c);return public(_get(c,ident))


def listing(store,archived=False):
    if type(archived) is not bool:raise ValueError('Укажите состояние архива')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');recover(c)
        rows=c.execute('SELECT * FROM content_oauth_connections WHERE archived=? ORDER BY updated_at DESC,id LIMIT 100',(archived,)).fetchall()
        return [public(dict(r)) for r in rows]


def check_local_credentials(store,vault,ident=None):
    """Reconcile restored/moved workspace without network calls or token rotation.

    Serialize with finish/disconnect so a concurrent refresh cannot invalidate a
    newly committed credential. Never delete a foreign workspace's exact blob.
    """
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');recover(c)
        if ident is not None:_get(c,ident)
        sql="SELECT * FROM content_oauth_connections WHERE state='connected'"
        records=c.execute(sql+(' AND id=?' if ident is not None else ''),(ident,) if ident is not None else ()).fetchall()
        for record in records:
            row=dict(record)
            try:
                value=vault.get(row['credential_ref'],binding(store,row)).value
                if value.get('issuer')!='https://clerk.higgsfield.ai' or value.get('resource')!='https://mcp.higgsfield.ai/mcp':raise HelperError('credential_unavailable')
            except HelperError:
                c.execute("UPDATE content_oauth_connections SET state='needs_login',revision=revision+1,last_error='credential_unavailable',updated_at=? WHERE id=?",(now(),row['id']))
                _event(c,row,'credential_unavailable')


def create(store,label,key):
    if not isinstance(label,str) or not label.strip() or len(label)>120 or '\x00' in label:raise ValueError('Укажите название подключения до 120 символов')
    if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('Нужен ключ создания')
    label=label.strip();digest=hashlib.sha256(label.encode()).hexdigest()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');prior=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if prior:
            if prior['command']!='oauth_create' or prior['payload_hash']!=digest:raise ValueError('request_conflict: ключ уже использован')
            return json.loads(prior['result'])
        if c.execute('SELECT count(*) FROM content_oauth_connections').fetchone()[0]>=100:raise ValueError('Допустимо до 100 подключений')
        ident=uuid.uuid4().hex
        c.execute('INSERT INTO content_oauth_connections VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,1,label,0,'higgsfield','disconnected',None,0,'{}',None,None,None,now(),None))
        row=_get(c,ident);_event(c,row,'created');result=public(row)
        c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'oauth_create',digest,dump(result)))
        return result


def update(store,ident,expected,*,label=None,archived=None):
    integer(expected,'Версия подключения',1)
    if label is not None and (not isinstance(label,str) or not label.strip() or len(label)>120 or '\x00' in label):raise ValueError('Укажите название до 120 символов')
    if archived is not None and type(archived) is not bool:raise ValueError('Укажите состояние архива')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');recover(c);row=_get(c,ident)
        if row['revision']!=expected:raise ValueError('revision_conflict: подключение изменено')
        if row['state'] in BUSY:raise ValueError('Дождитесь завершения или отмените вход')
        if row['archived'] and label is not None:raise ValueError('Сначала восстановите подключение')
        if archived and (row['state']=='connected' or row['credential_ref']):raise ValueError('Сначала отключите локальные credentials')
        name=row['label'] if label is None else label.strip();arc=row['archived'] if archived is None else int(archived)
        if name==row['label'] and arc==row['archived']:return public(row)
        c.execute('UPDATE content_oauth_connections SET revision=revision+1,label=?,archived=?,updated_at=? WHERE id=?',(name,arc,now(),ident))
        return public(_get(c,ident))


def begin(store,ident,expected,operation,key):
    if operation not in ('authorize','refresh'):raise ValueError('Неизвестная операция входа')
    integer(expected,'Версия подключения',1)
    if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('Нужен ключ операции')
    digest=hashlib.sha256(dump([ident,expected,operation]).encode()).hexdigest()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');recover(c)
        prior=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if prior:
            if prior['command']!='oauth_begin' or prior['payload_hash']!=digest:raise ValueError('request_conflict: ключ операции уже использован')
            return {**json.loads(prior['result']),'created':False}
        row=_get(c,ident)
        if row['revision']!=expected:raise ValueError('revision_conflict: подключение изменено')
        if row['archived']:raise ValueError('Подключение в архиве')
        if row['state'] in BUSY:raise ValueError('Операция подключения уже выполняется')
        if operation=='refresh' and (row['state']!='connected' or not row['credential_ref']):raise ValueError('Нужно заново выполнить вход')
        if operation=='authorize' and row['state']=='connected':raise ValueError('Перед входом в другой аккаунт отключите текущее подключение')
        intent=uuid.uuid4().hex;deadline=time.time()+(345 if operation=='authorize' else 65)
        c.execute('UPDATE content_oauth_connections SET revision=revision+1,state=?,intent_id=?,operation=?,deadline=?,updated_at=?,last_error=NULL WHERE id=?',('authenticating' if operation=='authorize' else 'verifying',intent,operation,deadline,now(),ident))
        result={'id':ident,'intent_id':intent};c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'oauth_begin',digest,dump(result)))
        _event(c,row,'login_started' if operation=='authorize' else 'verification_started');return {**result,'created':True}


def credential(store,vault,ident,intent):
    with store.db() as c:
        row=_get(c,ident)
        if row['intent_id']!=intent or row['operation']!='refresh' or row['state']!='verifying' or row['deadline']<=time.time():raise HelperError('superseded')
        value=vault.get(row['credential_ref'],binding(store,row))
        if value.value.get('issuer')!='https://clerk.higgsfield.ai' or value.value.get('resource')!='https://mcp.higgsfield.ai/mcp':raise HelperError('credential_unavailable')
        return value


def finish(store,vault,ident,intent,result):
    if not isinstance(result,LoginResult):raise HelperError('invalid_credential')
    # Apply the private IPC result contract again before storage.
    _result({'report':result.report,'credential':result.credential.value},'authorize')
    new_ref=None;old_ref=None;new_binding=None;old_binding=None
    try:
        with store.db() as c:
            c.execute('BEGIN IMMEDIATE');row=_get(c,ident)
            if row['intent_id']!=intent or row['state'] not in BUSY or row['deadline']<=time.time() or row['archived']:raise HelperError('superseded')
            new_binding=binding(store,row,row['credential_generation']+1);old_binding=binding(store,row)
            new_ref=vault.put(result.credential,new_binding);old_ref=row['credential_ref'];v=result.credential.value
            metadata={'resource':v['resource'],'issuer':v['issuer'],'client_id':v['client_info']['client_id'],
                'expires_at':v['expires_at'],'scopes':(v['tokens']['scope'] or '').split(),
                'tool_count':len(result.report['tools']),'checked_at':now()}
            c.execute("UPDATE content_oauth_connections SET revision=revision+1,state='connected',credential_ref=?,credential_generation=credential_generation+1,metadata=?,intent_id=NULL,operation=NULL,deadline=NULL,last_error=NULL,updated_at=? WHERE id=?",(new_ref,dump(metadata),now(),ident))
            row=_get(c,ident);_event(c,row,'connected');result_public=public(row)
    except Exception:
        if new_ref:
            try:vault.forget(new_ref,new_binding)
            except Exception:pass
        raise
    if old_ref:
        try:vault.forget(old_ref,old_binding)
        except Exception:pass  # unreachable orphan; never choose a credential by directory/name
    return result_public


def fail(store,ident,intent,code='operation_failed'):
    if code not in {'operation_failed','runtime_unavailable','timeout','cancelled','credential_unavailable','protocol_error','authorization_denied'}:code='operation_failed'
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=_get(c,ident)
        if row['intent_id']!=intent or row['state'] not in BUSY:return public(row)
        # Any interrupted refresh may already have rotated a remote token. No blind retry.
        state='needs_login' if row['operation']=='refresh' or row['credential_ref'] else 'disconnected'
        c.execute('UPDATE content_oauth_connections SET revision=revision+1,state=?,intent_id=NULL,operation=NULL,deadline=NULL,last_error=?,updated_at=? WHERE id=?',(state,code,now(),ident))
        _event(c,row,code);return public(_get(c,ident))


def disconnect(store,vault,ident,expected):
    integer(expected,'Версия подключения',1)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=_get(c,ident)
        if row['revision']!=expected:raise ValueError('revision_conflict: подключение изменено')
        old=row['credential_ref'];old_binding=binding(store,row)
        c.execute("UPDATE content_oauth_connections SET revision=revision+1,state='disconnected',credential_ref=NULL,credential_generation=credential_generation+1,metadata='{}',intent_id=NULL,operation=NULL,deadline=NULL,last_error=NULL,updated_at=? WHERE id=?",(now(),ident))
        row=_get(c,ident);_event(c,row,'disconnected');result=public(row)
    if old:
        try:vault.forget(old,old_binding)
        except Exception:pass
    return result


def history(store,ident):
    with store.db() as c:
        _get(c,ident)
        return [dict(r) for r in c.execute('SELECT generation,event,created_at FROM content_oauth_events WHERE connection_id=? ORDER BY created_at DESC,id DESC LIMIT 20',(ident,))]
