"""Internal adapter evidence reconciliation. Never exposed as a UI verified flag."""
import json
import time
import uuid
from .store import dump

def observe(store,ident,evidence,*,clock=time.time):
    if not isinstance(evidence,dict) or evidence.get('outcome') not in ('found','not_found'):raise ValueError('Неизвестный результат поиска публикации')
    if evidence['outcome']=='found' and (not isinstance(evidence.get('remote_id'),str) or not evidence['remote_id']):raise ValueError('Нужен ID найденной публикации')
    t=clock()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM content_outbox WHERE id=?',(ident,)).fetchone()
        if not row or row['state'] not in ('publication_unknown','cancel_requested','awaiting_moderation'):raise ValueError('Строка не ожидает сверки')
        if row['owner'] and (row['lease_until'] or 0)>t:raise ValueError('Worker ещё владеет отправкой')
        c.execute('INSERT INTO content_observations VALUES (?,?,?,?,?)',(uuid.uuid4().hex,ident,row['fence'],t,dump(evidence)))
        state=row['state'];receipt=json.loads(row['receipt'] or '{}')
        if evidence['outcome']=='found':
            # Found is not verified: a separate exact read-back is still required.
            receipt={**receipt,'outcome':'accepted','remote_id':evidence['remote_id'],'reconciled':True};state='verifying'
        elif evidence.get('strength')=='authoritative' and evidence.get('request_not_accepted') is True and evidence.get('evidence_ref'):
            attempt=c.execute('SELECT max(fence) FROM content_attempts WHERE outbox_id=?',(ident,)).fetchone()[0]
            acknowledged=bool(receipt.get('remote_id')) or any(json.loads(r[0]).get('remote_id') for r in c.execute('SELECT payload FROM content_observations WHERE outbox_id=?',(ident,)))
            if not acknowledged and evidence.get('submission_fence')==attempt:
                receipt={'outcome':'not_sent','evidence_ref':evidence['evidence_ref']};state='canceled' if row['cancel_requested'] else 'failed'
        c.execute('UPDATE content_outbox SET state=?,receipt=?,owner=NULL,lease_until=NULL,updated=? WHERE id=?',(state,dump(receipt),t,ident))
        return state
