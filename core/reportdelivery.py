"""Reports: explicit own endpoint, SMTP fallback, durable local pending delivery."""
import base64
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import egress

EMAIL = 'domatskevich@gmail.com'
SUBJECT = 'Ошибка обработки персональный агент'
_lock = threading.RLock()


def validate_url(value):
    value = str(value or '').strip()
    if not value:return ''
    u=urlparse(value)
    if not u.hostname or u.username or u.password or u.fragment or u.query or u.scheme not in ('http','https'):
        raise ValueError('Сервер отчётов: укажите полный URL без пароля, параметров и фрагмента')
    if u.scheme != 'https' and not egress.local_host(u.hostname):
        raise ValueError('Для внешнего сервера отчётов нужен HTTPS')
    return value


class ReportDelivery:
    def __init__(self,root,settings,mail):
        self.root=Path(root)/'report-outbox'; self.settings=settings; self.mail=mail

    def _write(self,record):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=self.root/(record['queue_id']+'.json'); tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8'); tmp.chmod(0o600); tmp.replace(path)

    def _read(self,qid):
        if not re.fullmatch(r'[a-f0-9]{32}',str(qid)):
            raise ValueError('Неизвестный отчёт')
        try:record=json.loads((self.root/(qid+'.json')).read_text())
        except (OSError,ValueError) as error:raise ValueError('Сохранённый отчёт не найден') from error
        if not isinstance(record,dict) or record.get('queue_id')!=qid:raise ValueError('Неверный идентификатор отчёта')
        if record.get('status')!='sent' and not isinstance(record.get('payload'),dict):
            raise ValueError('Сохранённый отчёт повреждён')
        return record

    def status(self):
        with _lock:
            records=[]
            if self.root.exists():
                for path in self.root.glob('*.json'):
                    try:
                        r=json.loads(path.read_text())
                        if not isinstance(r,dict) or r.get('queue_id')!=path.stem:continue
                        public={k:r.get(k) for k in ('queue_id','created','status','channel','reason')}
                        public['receipt']={k:v for k,v in (r.get('receipt') or {}).items() if k!='secret'}
                        records.append(public)
                    except (OSError,ValueError):continue
            records.sort(key=lambda r:r.get('created') or 0,reverse=True)
            pending=[r for r in records if r['status']!='sent']
            return {'email':EMAIL,'subject':SUBJECT,'mail_configured':bool(self.mail and self.mail.configured()),
                    'server_configured':bool(self.settings.get('report','server_url')),'pending':pending,
                    'recent':[r for r in records if r['status']=='sent'][:10]}

    def send(self,payload):
        with _lock:
            if len(self.status()['pending'])>=100:
                raise ValueError('Уже сохранено 100 неотправленных отчётов. Повторите отправку сохранённых или сохраните новый отчёт в файл.')
            qid=uuid.uuid4().hex
            payload=dict(payload,report_id=qid)
            record={'queue_id':qid,'created':time.time(),'status':'pending','channel':'','payload':payload,'receipt':{}}
            self._write(record)  # A transport error must not lose the report or its selected screenshot.
            return self._attempt(record)

    def retry(self,qid):
        with _lock:
            record=self._read(qid)
            if record['status']=='sent':return record['receipt']
            return self._attempt(record)

    def _attempt(self,record):
        payload=record['payload']; qid=record['queue_id']; reasons=[]
        url=os.environ.get('DEWIAR_REPORT_URL') or self.settings.get('report','server_url') or ''
        if url:
            accepted=None
            try:
                url=validate_url(url)
                host=urlparse(url).hostname
                egress.check_dewiar_host(host)
                if egress.local_only() and not egress.local_host(host):raise egress.LocalOnlyError(host)
                key=self.settings.get('report','server_key') or ''
                headers={'Idempotency-Key':qid}
                if key:headers['Authorization']='Bearer '+key
                response=httpx.post(url,json=payload,headers=headers,timeout=10,follow_redirects=False)
                data=response.json()
                if 200<=response.status_code<300 and data.get('ok') is True and isinstance(data.get('id'),(str,int)):
                    accepted=data
                else:reasons.append('Сервер не подтвердил приём отчёта')
            except Exception as error:
                reasons.append('Сервер недоступен или запрещён настройками ('+type(error).__name__+')')
            if accepted is not None:
                return self._sent(record,'server',accepted['id'],secret=accepted.get('secret',''))
        else:reasons.append('Сервер обработки не задан')
        if not self.mail or not self.mail.configured():
            reasons.append('Подключите почту отправителя: «Подключение → Почта»')
        else:
            try:
                host=(self.mail.data or {}).get('smtp_host')
                egress.check_dewiar_host(host)
                # The fixed Gmail recipient is external even when a local SMTP relay is configured.
                if egress.local_only():raise egress.LocalOnlyError(EMAIL)
                text=payload.get('text','')
                attachments=[('report.txt','text/plain',text.encode('utf-8'))]
                if payload.get('shot'):
                    prefix,encoded=payload['shot'].split(',',1)
                    if prefix!='data:image/jpeg;base64':raise ValueError('Неверный формат снимка')
                    attachments.append(('screenshot.jpg','image/jpeg',base64.b64decode(encoded,validate=True)))
                body='Отчёт Dewiar\nНомер: '+qid+'\n\n'+(payload.get('note') or payload.get('summary') or '')
                if payload.get('who'):body+='\n\nАвтор: '+payload['who']
                self.mail.send(EMAIL,SUBJECT,body,attachment_data=attachments,message_id=qid)
            except Exception as error:
                reasons.append('Почта не подтвердила отправку ('+type(error).__name__+'). Проверьте подключение и настройки безопасности')
            else:
                return self._sent(record,'email','email-'+qid)
        record.update(status='pending',reason='. '.join(reasons),channel='queued')
        self._write(record)
        return {'id':'local-'+qid,'queue_id':qid,'delivery':'queued','email':EMAIL,'subject':SUBJECT,'reason':record['reason']}

    def _sent(self,record,channel,rid,secret=''):
        receipt={'id':rid,'secret':secret,'queue_id':record['queue_id'],'delivery':channel,'email':EMAIL,'subject':SUBJECT}
        record.update(status='sent',channel=channel,receipt=receipt,reason='')
        record.pop('payload',None)  # Retain status, not a second permanent copy of logs/screenshots.
        self._write(record)
        return receipt
