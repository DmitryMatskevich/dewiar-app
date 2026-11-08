"""One bounded outbox step with an injected adapter; no default/live provider.

Adapter preflight must enforce verified account capability and cost budget.
Publishing and read-back run outside SQLite transactions. Production scheduling
must explicitly construct this worker after OAuth and trusted UI integration.
"""
import json
import threading
import time
from . import queue
from .store import dump

def process_one(store,adapter,owner,*,clock=time.time):
    job=queue.claim(store,owner,clock=clock)
    if not job:return None
    stopped=threading.Event()
    def beat():
        while not stopped.wait(15):
            try:queue.heartbeat(store,job,clock=clock)
            except ValueError:return
    pulse=threading.Thread(target=beat,daemon=True,name='content-lease');pulse.start()
    try:
        if job['state']=='verifying':
            with store.db() as c:payload=json.loads(c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(job['package_hash'],)).fetchone()[0])
            receipt=json.loads(job['receipt'])
            try:evidence=adapter.read_back(payload,receipt['remote_id'])
            except Exception:evidence=None
            if evidence is not None:
                queue.verify(store,job,evidence,clock=clock)
            else:
                with store.db() as c:
                    c.execute('BEGIN IMMEDIATE');queue._owned(c,job,clock(),('verifying',));count=receipt.get('read_attempts',0)+1
                    state='publication_unknown' if count>=10 else 'verifying'
                    c.execute('UPDATE content_outbox SET state=?,owner=NULL,lease_until=NULL,due=?,receipt=?,updated=? WHERE id=?',(state,clock()+30,dump({**receipt,'read_attempts':count}),clock(),job['id']))
        else:
            with store.db() as c:payload=json.loads(c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(job['package_hash'],)).fetchone()[0])
            try:adapter.preflight(payload)
            except Exception:
                with store.db() as c:
                    c.execute('BEGIN IMMEDIATE');queue._owned(c,job,clock(),('claimed',))
                    c.execute("UPDATE content_outbox SET state='needs_review',owner=NULL,lease_until=NULL,error='Адаптер не подтвердил доступ, формат или бюджет',updated=? WHERE id=?",(clock(),job['id']))
                return queue.get(store,job['id'])
            payload=queue.begin_send(store,job,clock=clock)
            if payload is not None:
                try:
                    result=adapter.publish(payload,request_key=job['id'])
                    if not isinstance(result,dict) or not isinstance(result.get('remote_id'),str) or not result['remote_id']:raise ValueError('No remote ID')
                    result={'outcome':'accepted','remote_id':result['remote_id']}
                except queue.DefinitelyNotSent:result={'outcome':'not_sent','reason':'Адаптер подтвердил неотправку'}
                except Exception:result={'outcome':'unknown','reason':'Результат отправки неизвестен; автоматический повтор запрещён'}
                queue.finish_send(store,job,result,clock=clock)
        return queue.get(store,job['id'])
    finally:stopped.set();pulse.join(1)
