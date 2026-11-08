"""Durable text outbox. No provider registration or external execution at import.

Scope recording is an internal integration boundary, deliberately absent from
the generic command dispatcher until an authenticated UI action is connected.
"""
import hashlib
import json
import time
import uuid
from .store import dump,integer
from . import distribution,packages,schedule

class DefinitelyNotSent(Exception):pass

def migrate(c):
    c.execute('CREATE TABLE content_scopes(id TEXT PRIMARY KEY,hashes TEXT NOT NULL,actor TEXT NOT NULL,expires REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0)')
    c.execute('CREATE TABLE content_outbox(id TEXT PRIMARY KEY,package_hash TEXT NOT NULL UNIQUE REFERENCES content_packages(hash),scope_id TEXT NOT NULL REFERENCES content_scopes(id),destination_id TEXT NOT NULL,state TEXT NOT NULL,due REAL NOT NULL,timezone TEXT NOT NULL,overdue_window INTEGER NOT NULL,owner TEXT,lease_until REAL,fence INTEGER NOT NULL DEFAULT 0,cancel_requested INTEGER NOT NULL DEFAULT 0,receipt TEXT,error TEXT,updated REAL NOT NULL)')
    c.execute('CREATE INDEX content_outbox_due ON content_outbox(state,due)')
    c.execute('CREATE TABLE content_attempts(id TEXT PRIMARY KEY,outbox_id TEXT NOT NULL REFERENCES content_outbox(id),fence INTEGER NOT NULL,started REAL NOT NULL,result TEXT)')
    c.execute('CREATE TABLE content_observations(id TEXT PRIMARY KEY,outbox_id TEXT NOT NULL REFERENCES content_outbox(id),fence INTEGER NOT NULL,observed REAL NOT NULL,payload TEXT NOT NULL)')

def record_scope_from_trusted_ui(store,hashes,actor,expires,*,clock=time.time):
    # Internal only. Never expose a confirmed=true wire command for this function.
    if not isinstance(actor,str) or not actor.strip() or len(actor)>240:raise ValueError('Нужен проверенный инициатор действия')
    hashes=distribution.ids(hashes,'Пакеты')
    if not hashes or type(expires) not in (int,float) or not clock()<expires<=clock()+31*86400:raise ValueError('Срок разрешения не более 31 дня')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        for digest in hashes:
            row=c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(digest,)).fetchone()
            if not row:raise ValueError('Пакет не найден')
            _current(c,digest,json.loads(row['snapshot']))
        ident=uuid.uuid4().hex;c.execute('INSERT INTO content_scopes VALUES (?,?,?,?,0)',(ident,dump(hashes),actor,expires))
    return ident

def _current(c,digest,payload):
    variant=distribution._variant(c,payload['variant_id'])
    if variant['revision']!=payload['variant_revision']:raise ValueError('stale_package: текст варианта изменился')
    prepared=packages.prepare_in(c,variant)
    if prepared['hash']!=digest:raise ValueError('stale_package: содержимое или назначение изменилось')

def _preflight(c,row,t):
    scope=c.execute('SELECT * FROM content_scopes WHERE id=?',(row['scope_id'],)).fetchone()
    if not scope or scope['revoked'] or scope['expires']<=t or row['package_hash'] not in json.loads(scope['hashes']):raise ValueError('Разрешение истекло, отозвано или не включает пакет')
    payload=json.loads(c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(row['package_hash'],)).fetchone()[0]);_current(c,row['package_hash'],payload)
    return payload

def enqueue(store,digest,scope_id,scheduled_at,timezone_name='Europe/Moscow',overdue_window=300,*,clock=time.time):
    due=schedule.instant(scheduled_at,timezone_name);window=integer(overdue_window,'Окно опоздания',0,43200);t=clock()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM content_outbox WHERE package_hash=?',(digest,)).fetchone()
        if old:
            if old['due']!=due or old['timezone']!=timezone_name or old['overdue_window']!=window:raise ValueError('request_conflict: этот пакет уже запланирован иначе')
            return dict(old)
        payload_row=c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(digest,)).fetchone()
        if not payload_row:raise ValueError('Пакет не найден')
        payload=json.loads(payload_row[0]);row={'id':uuid.uuid4().hex,'package_hash':digest,'scope_id':scope_id,'destination_id':payload['target']['destination']['id'],'state':'scheduled' if due>t else 'queued','due':due,'timezone':timezone_name,'overdue_window':window,'owner':None,'lease_until':None,'fence':0,'cancel_requested':0,'receipt':None,'error':None,'updated':t}
        _preflight(c,row,t)
        if c.execute('SELECT expires FROM content_scopes WHERE id=?',(scope_id,)).fetchone()[0]<=due:raise ValueError('Разрешение истекает раньше выбранного времени')
        c.execute('INSERT INTO content_outbox VALUES (:id,:package_hash,:scope_id,:destination_id,:state,:due,:timezone,:overdue_window,:owner,:lease_until,:fence,:cancel_requested,:receipt,:error,:updated)',row)
        return row

def get(store,ident):
    with store.db() as c:
        row=c.execute('SELECT * FROM content_outbox WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Строка очереди не найдена')
        result=dict(row);result['receipt']=json.loads(result['receipt']) if result['receipt'] else None;return result

def _recover(c,t):
    # A claimed row has not crossed the persisted send boundary yet.
    c.execute("UPDATE content_outbox SET state='queued',owner=NULL,lease_until=NULL,updated=? WHERE state='claimed' AND lease_until<=?",(t,t))
    c.execute("UPDATE content_outbox SET state='publication_unknown',owner=NULL,lease_until=NULL,error='Истекла аренда после начала отправки; нужен reconcile',updated=? WHERE state IN ('publishing','cancel_requested') AND lease_until<=?",(t,t))
    c.execute("UPDATE content_outbox SET owner=NULL,lease_until=NULL WHERE state='verifying' AND lease_until<=?",(t,))

def claim(store,owner,*,clock=time.time):
    if not isinstance(owner,str) or not owner or len(owner)>128:raise ValueError('Нужен ID worker')
    t=clock()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');_recover(c,t)
        if c.execute("SELECT 1 FROM content_outbox WHERE owner IS NOT NULL AND lease_until>?",(t,)).fetchone():return None
        rows=c.execute("SELECT * FROM content_outbox WHERE state IN ('queued','scheduled','verifying') AND due<=? AND owner IS NULL ORDER BY due,id",(t,)).fetchall()
        for raw in rows:
            row=dict(raw)
            # Verification is a read-back of an already sent package; it must survive scope expiry or text edits.
            if row['state']!='verifying':
                try:
                    if t-row['due']>row['overdue_window']:raise ValueError('Пропущено окно расписания; нужен новый выбор времени')
                    _preflight(c,row,t)
                except ValueError as e:
                    c.execute("UPDATE content_outbox SET state='needs_review',error=?,updated=? WHERE id=?",(str(e),t,row['id']));continue
            state='verifying' if row['state']=='verifying' else 'claimed';fence=row['fence']+1
            c.execute('UPDATE content_outbox SET state=?,owner=?,lease_until=?,fence=?,updated=? WHERE id=?',(state,owner,t+60,fence,t,row['id']))
            return {**row,'state':state,'owner':owner,'lease_until':t+60,'fence':fence}
    return None

def _owned(c,claim,t,states):
    row=c.execute('SELECT * FROM content_outbox WHERE id=?',(claim['id'],)).fetchone()
    if not row or row['owner']!=claim['owner'] or row['fence']!=claim['fence'] or row['lease_until']<=t or row['state'] not in states:raise ValueError('lease_lost: результат принадлежит устаревшему worker')
    return dict(row)

def heartbeat(store,job,*,clock=time.time):
    t=clock()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');_owned(c,job,t,('claimed','publishing','cancel_requested','verifying'))
        c.execute('UPDATE content_outbox SET lease_until=? WHERE id=?',(t+60,job['id']))

def begin_send(store,job,*,clock=time.time):
    t=clock()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=_owned(c,job,t,('claimed',))
        if row['cancel_requested']:raise ValueError('Отправка отменена')
        try:payload=_preflight(c,row,t)
        except ValueError as e:
            c.execute("UPDATE content_outbox SET state='needs_review',owner=NULL,lease_until=NULL,error=?,updated=? WHERE id=?",(str(e),t,row['id']));return None
        c.execute("UPDATE content_outbox SET state='publishing',updated=? WHERE id=?",(t,row['id']))
        c.execute('INSERT INTO content_attempts VALUES (?,?,?,?,NULL)',(uuid.uuid4().hex,row['id'],row['fence'],t));return payload

def finish_send(store,job,result,*,clock=time.time):
    t=clock()
    if not isinstance(result,dict) or result.get('outcome') not in ('accepted','not_sent','unknown'):raise ValueError('Некорректный результат отправки')
    if result['outcome']=='accepted' and (not isinstance(result.get('remote_id'),str) or not result['remote_id']):raise ValueError('Ответ принятия должен содержать remote ID')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:_owned(c,job,t,('publishing','cancel_requested'))
        except ValueError:
            c.execute('INSERT INTO content_observations VALUES (?,?,?,?,?)',(uuid.uuid4().hex,job['id'],job['fence'],t,dump(result)));return False
        state={'accepted':'verifying','not_sent':'failed','unknown':'publication_unknown'}[result['outcome']]
        c.execute('UPDATE content_outbox SET state=?,owner=NULL,lease_until=NULL,receipt=?,updated=? WHERE id=?',(state,dump(result),t,job['id']))
        c.execute('UPDATE content_attempts SET result=? WHERE outbox_id=? AND fence=?',(dump(result),job['id'],job['fence']));return True

def cancel(store,ident,*,clock=time.time):
    t=clock()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM content_outbox WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Строка очереди не найдена')
        if row['state'] in ('scheduled','queued','claimed','needs_review','failed'):
            c.execute("UPDATE content_outbox SET state='canceled',cancel_requested=1,owner=NULL,lease_until=NULL,updated=? WHERE id=?",(t,ident))
        elif row['state'] in ('publishing','publication_unknown','cancel_requested'):
            c.execute("UPDATE content_outbox SET state='cancel_requested',cancel_requested=1,updated=? WHERE id=?",(t,ident))
        elif row['state'] in ('verifying','awaiting_moderation'):
            c.execute('UPDATE content_outbox SET cancel_requested=1,updated=? WHERE id=?',(t,ident))
    return get(store,ident)

def retry(store,ident,scope_id,scheduled_at,timezone_name='Europe/Moscow',*,clock=time.time):
    t=clock();due=schedule.instant(scheduled_at,timezone_name)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM content_outbox WHERE id=?',(ident,)).fetchone()
        if not row or row['state']!='failed' or json.loads(row['receipt'] or '{}').get('outcome')!='not_sent':raise ValueError('Повтор разрешён только при доказанной неотправке')
        candidate={**dict(row),'scope_id':scope_id};_preflight(c,candidate,t)
        c.execute("UPDATE content_outbox SET state='queued',scope_id=?,due=?,timezone=?,cancel_requested=0,owner=NULL,lease_until=NULL,error=NULL,updated=? WHERE id=?",(scope_id,due,timezone_name,t,ident))
    return get(store,ident)

def verify(store,job,evidence,*,clock=time.time):
    t=clock()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=_owned(c,job,t,('verifying',));payload=json.loads(c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(row['package_hash'],)).fetchone()[0]);receipt=json.loads(row['receipt'])
        expected={'remote_id':receipt['remote_id'],'identity_id':payload['target']['identity']['id'],'destination_id':payload['target']['destination']['id'],'body_sha256':hashlib.sha256(payload['text'].encode()).hexdigest()}
        if not isinstance(evidence,dict) or any(evidence.get(k)!=v for k,v in expected.items()):
            state='publication_unknown'
        else:state='awaiting_moderation' if evidence.get('moderation_pending') else 'published'
        c.execute('UPDATE content_outbox SET state=?,owner=NULL,lease_until=NULL,receipt=?,updated=? WHERE id=?',(state,dump({**receipt,'verification':evidence}),t,row['id']))
        return state
