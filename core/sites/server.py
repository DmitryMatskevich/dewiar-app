"""Loopback web UI and authenticated JSON API for independent site worker."""
import argparse
import fcntl
import hmac
import json
import logging
import mimetypes
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, date
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .store import Store, minute
from .runtime import Runtime
from . import access, discovery

UI = Path(__file__).resolve().parents[2] / 'ui' / 'sites'


class App:
    def __init__(self, root, config, test=False):
        self.store = Store(root)
        self.config, self.test = Path(config), test
        self.runtime = Runtime(self.store, test, self.config / 'settings.json')
        self.token_path = Path(root) / 'api-token'
        if not self.token_path.exists():
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_urlsafe(32))
        self.token = self.token_path.read_text().strip()
        reader_path = Path(root) / 'reader-token'
        if not reader_path.exists():
            fd = os.open(reader_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_urlsafe(32))
        self.reader_token = reader_path.read_text().strip()
        # Interrupted deliveries are ambiguous, never silently resent.
        with self.store.db() as c:
            c.execute("UPDATE outbox SET state='uncertain',error='Процесс остановлен во время отправки' WHERE state='sending'")

    def summary(self, days=30):
        now = minute()
        local_day = datetime.now(ZoneInfo('Europe/Moscow')).date()
        out = []
        for s in self.store.all():
            linked=self.store.asset(s.get('asset_id'))
            if linked:
                s['server_name'],s['server_ip']=linked['name'],linked['address']
            elif s.get('server_ip') and not s.get('server_name'):
                s['server_name']='Узел '+s['server_ip']
            for key in ('hosting_expires','domain_expires'):
                s[key+'_in_days'] = (date.fromisoformat(s[key])-local_day).days if s.get(key) else None
            rows = self.store.results(s['id'])
            current = next((r for r in rows if r['probe'] == 'web'), None)
            state = current['state'] if current and current['until_min'] > now else 'UNKNOWN'
            if state == 'DOWN' and current['failures'] == 1:
                state = 'PENDING'
            if not s['enabled'] or self.store.settings()['paused'] or self.runtime.local_only():
                state = 'PAUSED'
            history = self.store.history(s, s['created'] if days == 0 else now-days*1440, now)
            history.pop('bars')
            out.append(dict(s, state=state, results=rows, stats=history))
        return out

    def get(self, path, query):
        if path == '/api/mobile/summary':
            items = self.summary(30)
            return {'schema_version': 1, 'observed_at': minute(),
                    'observer': {'heartbeat': self.runtime.tick, 'paused': self.store.settings()['paused'], 'policy_paused': self.runtime.local_only()},
                    'items': [{k:s[k] for k in ('id','name','url','state','stats','hosting_expires','hosting_expires_in_days','domain_expires','domain_expires_in_days')} | {'last_checked':max((r['at'] for r in s['results']), default=None)} for s in items]}
        if path == '/api/sites':
            return {'items': self.summary(max(0,min(36500,int(query.get('days',['30'])[0]))))}
        if path == '/api/providers':
            return {'items': self.store.providers()}
        if path == '/api/status':
            return dict(self.runtime.status(), test=self.test, settings=self.store.settings())
        if path == '/api/access':
            return access.catalog(self.config)
        if path == '/api/assets':
            return {'items':self.store.assets()}
        if path == '/api/alerts':
            items=self.summary(1); now=minute(); alerts=[]
            for site in items:
                reasons=[]
                if site['state'] in ('DOWN','PENDING'):
                    reasons.append('Сайт '+('недоступен' if site['state']=='DOWN' else 'перепроверяется'))
                for result in site['results']:
                    if result['state']=='DOWN' and result['until_min']>now:
                        reasons.append('Сбой '+str(result['probe']))
                for key,label in (('hosting_expires_in_days','Оплата хостинга'),('domain_expires_in_days','Оплата домена')):
                    days=site.get(key)
                    if days is not None and days<=7:
                        reasons.append(label+(' просрочена' if days<0 else ' скоро'))
                if reasons:
                    alerts.append({'id':site['id'],'name':site['name'],'reasons':list(dict.fromkeys(reasons))})
            return {'count':len(alerts),'items':alerts,'updated_at':now}
        if path == '/api/incidents':
            return {'items': self.store.incidents(query.get('site',[None])[0])}
        if path == '/api/deliveries':
            return {'items': self.store.deliveries()}
        if path == '/api/history':
            s = self.store.get(query.get('site',[''])[0])
            if not s:
                raise ValueError('Сайт не найден')
            start = int(query.get('start',[minute()-30*1440])[0]); end = int(query.get('end',[minute()])[0])
            if start > end or end > minute() or end-start > 36500*1440:
                raise ValueError('Неверный период')
            return self.store.history(s, start, end)
        raise ValueError('Неизвестный запрос')

    def post(self, path, data):
        if path == '/api/sites':
            saved = self.store.save(data)
            return {'item': saved}
        if path == '/api/providers':
            return {'item': self.store.save_provider(data)}
        if path == '/api/assets':
            return {'item':self.store.save_asset(data.get('name'),data.get('address'),data.get('provider',''))}
        if path == '/api/check':
            site = self.store.get(data.get('id'))
            if not site:
                raise ValueError('Сайт не найден')
            return {'job_id': self.runtime.submit(site, True)}
        if path == '/api/settings':
            return {'settings': self.store.save_settings(data)}
        if path == '/api/reveal':
            # Explicit user action only; no automatic fetching or persistent client cache.
            return access.reveal(self.config, str(data.get('id','')))
        if path == '/api/discover':
            # User-triggered public lookup only; discovery never reads credentials or changes a site record.
            return discovery.discover(str(data.get('domain', '')))
        if path == '/api/notify-test':
            channel = data.get('channel')
            if channel not in ('email', 'telegram', 'max', 'simulation'):
                raise ValueError('Неизвестный канал')
            uid = uuid.uuid4().hex
            with self.store.db() as c:
                c.execute('INSERT INTO outbox(id,dedupe,channel,body,state,due,target) VALUES (?,?,?,?,?,?,?)', (uid, 'test:'+uid, channel, 'Dewiar · Проверка канала уведомлений. Это не авария.', 'pending', minute(), json.dumps(self.store.settings())))
            return {'id': uid}
        raise ValueError('Неизвестная операция')

    def dispatch_loop(self):
        while not self.runtime.stop.wait(2):
            if self.runtime.local_only():
                continue
            with self.store.db() as c:
                row = c.execute("SELECT * FROM outbox WHERE state='pending' AND due<=? ORDER BY rowid LIMIT 1", (minute(),)).fetchone()
                if not row:
                    continue
                row = dict(row)
                c.execute("UPDATE outbox SET state='sending' WHERE id=?", (row['id'],))
            state, error = 'uncertain', ''
            try:
                # Delivery has its own process/deadline, never holds a probe slot.
                payload = dict(channel=row['channel'], body=row['body'], config=str(self.config), settings=json.loads(row.get('target') or '{}') or self.store.settings(), message_id=row['id'])
                p = subprocess.run([sys.executable, '-B', '-m', 'sites.delivery'], input=json.dumps(payload), text=True, capture_output=True, timeout=35, cwd=str(Path(__file__).resolve().parents[1]), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', DEWIAR_CONFIG_DIR=str(self.config)))
                result = json.loads(p.stdout) if p.returncode == 0 else {'state':'failed', 'error':'Не удалось запустить отправитель'}
                state, error = result['state'], result.get('error','')
            except (OSError, ValueError, subprocess.TimeoutExpired):
                error = 'Неизвестен результат отправки; автоматический повтор остановлен'
            tries = row['tries'] + 1
            if state == 'failed' and tries < 5:
                state = 'pending'
            with self.store.db() as c:
                c.execute('UPDATE outbox SET state=?,tries=?,due=?,error=? WHERE id=?', (state, tries, minute()+[1,5,15,60,120][min(tries-1,4)], error[:200], row['id']))


class Handler(BaseHTTPRequestHandler):
    server_version = 'DewiarSites/1'
    def log_message(self, *args):
        pass

    def reply(self, code, body, kind='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        host = self.headers.get('Host','')
        allowed = f'127.0.0.1:{self.server.server_port}'
        if host != allowed:
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + allowed:
            return False
        supplied = self.headers.get('Authorization','')
        if self.command == 'GET' and urlsplit(self.path).path == '/api/mobile/summary':
            if hmac.compare_digest(supplied, 'Bearer '+self.server.app.reader_token):
                return True
        return hmac.compare_digest(supplied, 'Bearer '+self.server.app.token)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path.startswith('/api/'):
            if not self.authorized():
                return self.reply(401, {'error':'Откройте раздел из Dewiar или локального ярлыка'})
            try:
                self.reply(200, self.server.app.get(u.path, parse_qs(u.query)))
            except (ValueError, TypeError) as e:
                self.reply(400, {'error':str(e)[:200]})
            except Exception:
                logging.exception('sites API read failed')
                self.reply(500, {'error':'Не удалось прочитать данные мониторинга'})
            return
        files = {'/':'index.html', '/app.js':'app.js', '/style.css':'style.css'}
        if u.path in files:
            p = UI / files[u.path]
            return self.reply(200, p.read_bytes(), (mimetypes.guess_type(p)[0] or 'text/plain')+'; charset=utf-8')
        self.reply(404, {'error':'Не найдено'})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {'error':'Нет доступа'})
        try:
            n = int(self.headers.get('Content-Length','0'))
            if not 0 < n <= 131072:
                return self.reply(413, {'error':'Слишком большой запрос'})
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict):
                raise ValueError('Ожидается объект')
            self.reply(200, self.server.app.post(urlsplit(self.path).path, data))
        except (ValueError, TypeError) as e:
            self.reply(400, {'error':str(e)[:200]})
        except Exception:
            self.reply(500, {'error':'Операция не выполнена; проверьте состояние процесса'})


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--config', required=True)
    p.add_argument('--port', type=int, default=8847)
    p.add_argument('--test', action='store_true')
    args = p.parse_args()
    root = Path(args.root); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(root/'worker.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Мониторинг уже запущен для этой базы')
    os.umask(0o077)
    try:
        os.nice(5)
    except OSError:
        pass
    app = App(root, args.config, args.test)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.app = app
    (root/'endpoint.json').write_text(json.dumps({'port':server.server_port,'pid':os.getpid()}))
    def stop(*_):
        app.runtime.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    app.runtime.start()
    threading.Thread(target=app.dispatch_loop, name='sites-delivery', daemon=True).start()
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        app.runtime.stop.set()
        app.runtime.pool.shutdown(wait=True, cancel_futures=True)
        server.server_close()
        lock.close()


if __name__ == '__main__':
    main()
