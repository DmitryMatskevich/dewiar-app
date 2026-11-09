"""Compact durable observations and incident outbox. All dates are UTC minutes."""
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo
from contextlib import contextmanager
import os
import ipaddress
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def minute():
    return int(time.time() // 60)


def web_url(value):
    value = str(value).strip()
    if '://' not in value:
        value = 'https://' + value
    u = urlsplit(value)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.fragment:
        raise ValueError('Нужен HTTP(S) адрес без пароля и фрагмента')
    if len(value) > 2048:
        raise ValueError('Адрес слишком длинный')
    try:
        _ = u.port
        host = u.hostname.encode('idna').decode('ascii').lower()
    except (ValueError, UnicodeError) as e:
        raise ValueError('Неверный адрес или порт') from e
    if host in ('localhost',) or '%' in host:
        raise ValueError('Для сайта нужен публичный домен')
    return value


def network_host(value, field):
    value = value.strip().rstrip('.')
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        try:
            host = value.encode('idna').decode('ascii').lower()
        except UnicodeError as e:
            raise ValueError('Неверное имя: ' + field) from e
        if len(host) > 253 or not host or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.')):
            raise ValueError('Неверное имя: ' + field)
        return host


def validate_provider(data):
    fields = ('name', 'website_url', 'panel_url', 'panel_name', 'source_url', 'notes')
    d = {k: str(data.get(k) or '').strip() for k in fields}
    if not d['name'] or len(d['name']) > 160:
        raise ValueError('Укажите название провайдера (до 160 символов)')
    for k in ('website_url', 'panel_url', 'source_url'):
        if d[k]:
            d[k] = web_url(d[k])
    if len(d['panel_name']) > 160 or len(d['notes']) > 2000:
        raise ValueError('Слишком длинное поле провайдера')
    contacts = data.get('contacts', [])
    if not isinstance(contacts, list) or len(contacts) > 30:
        raise ValueError('Допустимо не более 30 каналов поддержки')
    allowed = {'telegram', 'ticket', 'email', 'phone', 'chat', 'news', 'status', 'feedback', 'complaint', 'abuse', 'other'}
    d['contacts'] = []
    for item in contacts:
        if not isinstance(item, dict) or item.get('type') not in allowed:
            raise ValueError('Неверный тип контакта поддержки')
        contact = {k: str(item.get(k) or '').strip() for k in ('type', 'label', 'value', 'url')}
        if not contact['value'] or len(contact['value']) > 500 or len(contact['label']) > 120:
            raise ValueError('Проверьте подпись и значение контакта')
        if contact['url']:
            contact['url'] = web_url(contact['url'])
        if contact['type'] == 'email' and ('@' not in contact['value'] or len(contact['value']) > 254):
            raise ValueError('Некорректный email поддержки')
        d['contacts'].append(contact)
    return d


def validate(data):
    d = {k: data.get(k, '') for k in ('name', 'url', 'hoster', 'hoster_url', 'hosting_provider_id', 'panel_url', 'panel_name', 'vault_id', 'hosting_vault_id', 'dns_vault_id', 'server_id', 'asset_id', 'server_name', 'server_ip', 'network_operator', 'registrar', 'hosting_candidate', 'dns_provider', 'dns_provider_url', 'dns_panel_url', 'dns_panel_name', 'dns_nameservers', 'dns_mx_hosts', 'dns_host', 'mail_host', 'mail_provider', 'mail_imap_host', 'mail_smtp_host', 'mail_mx_host', 'notes', 'health_url', 'console_url', 'marker', 'ssh_host', 'hosting_expires', 'domain_expires')}
    for k, v in d.items():
        if not isinstance(v, str) or len(v) > (2000 if k == 'notes' else 2048):
            raise ValueError('Некорректное поле ' + k)
        d[k] = v.strip()
    d['url'] = web_url(d['url']) if d['url'] else ''
    hostname = urlsplit(d['url']).hostname if d['url'] else ''
    d['name'] = (d['name'] or hostname)[:160]
    if not d['name']:
        raise ValueError('Укажите название сайта или почтового узла')
    for k in ('hosting_expires', 'domain_expires'):
        if d[k]:
            try:
                if date.fromisoformat(d[k]).isoformat() != d[k]: raise ValueError()
            except ValueError as e:
                raise ValueError('Укажите дату окончания в формате ГГГГ-ММ-ДД') from e
    for k in ('dns_host', 'ssh_host', 'mail_host', 'mail_imap_host', 'mail_smtp_host', 'mail_mx_host', 'server_ip'):
        if d[k]:
            d[k] = network_host(d[k], k)
    for k in ('hoster_url', 'panel_url', 'dns_provider_url', 'dns_panel_url', 'health_url', 'console_url'):
        if d[k]:
            d[k] = web_url(d[k])
    for k, default, lo, hi in [('period', 5, 1, 1440), ('ssh_port', 22, 1, 65535), ('ftp_port', 21, 1, 65535), ('mail_port', 993, 1, 65535), ('mail_smtp_port', 587, 1, 65535)]:
        try:
            d[k] = int(data.get(k, default))
        except (TypeError, ValueError) as e:
            raise ValueError('Неверное число ' + k) from e
        if not lo <= d[k] <= hi:
            raise ValueError(f'{k}: допустимо {lo}–{hi}')
    for k, default in [('enabled', True), ('dns', True), ('ping', False), ('ssh', False), ('ftp', False), ('mail', False), ('health_required', False), ('check_http', True), ('check_https', True)]:
        if not isinstance(data.get(k, default), bool):
            raise ValueError('Неверный переключатель ' + k)
        d[k] = data.get(k, default)
    d['mail_kind'] = data.get('mail_kind', 'imap_tls')
    if d['mail_kind'] not in ('imap_tls', 'smtp_tls', 'smtp_starttls', 'smtp'):
        raise ValueError('Неверный тип почты')
    d['mail_smtp_kind']=data.get('mail_smtp_kind','smtp_starttls')
    if d['mail_smtp_kind'] not in ('smtp_tls','smtp_starttls'):
        raise ValueError('Неверный протокол SMTP')
    target_host = hostname or d['dns_host'] or d['mail_host'] or d['mail_imap_host'] or d['mail_smtp_host'] or d['mail_mx_host'] or d['ssh_host']
    # Site cards can be saved as inventory/drafts. Require a target only for
    # checks that are actually enabled; a paused card needs no probe targets.
    if d['enabled']:
        if d['mail'] and not any(d[k] for k in ('mail_host','mail_imap_host','mail_smtp_host','mail_mx_host')):
            raise ValueError('Для проверки почты укажите IMAP, SMTP или MX сервер')
        if d['dns'] and not target_host:
            raise ValueError('Для проверки DNS укажите домен или сервер')
        if d['ping'] and not target_host:
            raise ValueError('Для проверки IP укажите домен или сервер')
        if (d['ssh'] or d['ftp']) and not (d['ssh_host'] or hostname or d['dns_host']):
            raise ValueError('Для проверки SSH или FTP укажите домен или сервер')
        if d['health_required'] and not d['health_url']:
            raise ValueError('Для проверки HTML укажите URL тестовой страницы')
    return d


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'sites.db'
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sites(id TEXT PRIMARY KEY, rev INTEGER NOT NULL, created INTEGER NOT NULL, due INTEGER NOT NULL, config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS current(site TEXT NOT NULL, probe TEXT NOT NULL, state TEXT NOT NULL, at INTEGER NOT NULL, until_min INTEGER NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, latency INTEGER, failures INTEGER NOT NULL, successes INTEGER NOT NULL, PRIMARY KEY(site,probe));
            CREATE TABLE IF NOT EXISTS port_diagnostics(site TEXT NOT NULL, probe TEXT NOT NULL CHECK(probe IN ('ssh','ftp')), state TEXT NOT NULL, at INTEGER NOT NULL, until_min INTEGER NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, latency INTEGER, PRIMARY KEY(site,probe));
            CREATE TABLE IF NOT EXISTS spans(id INTEGER PRIMARY KEY, site TEXT NOT NULL, probe TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL CHECK(end>=start), state TEXT NOT NULL, rev INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS span_lookup ON spans(site,probe,start);
            CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, site TEXT NOT NULL, probe TEXT NOT NULL, start INTEGER NOT NULL, confirmed INTEGER NOT NULL, recovered INTEGER, closed INTEGER, reason TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS incident_open ON incidents(site,probe) WHERE closed IS NULL;
            CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, site TEXT, channel TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL, tries INTEGER NOT NULL DEFAULT 0, due INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS exclusions(id INTEGER PRIMARY KEY, site TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at INTEGER NOT NULL, action TEXT NOT NULL, site TEXT);
            CREATE TABLE IF NOT EXISTS renewal_alerts(site TEXT NOT NULL, kind TEXT NOT NULL, deadline TEXT NOT NULL, threshold INTEGER NOT NULL, at INTEGER NOT NULL, PRIMARY KEY(site,kind,deadline,threshold));
            CREATE TABLE IF NOT EXISTS server_assets(id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL UNIQUE, provider TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY, rev INTEGER NOT NULL, created INTEGER NOT NULL, updated INTEGER NOT NULL, config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_diagnostics(provider TEXT PRIMARY KEY, rev INTEGER NOT NULL, state TEXT NOT NULL, at INTEGER NOT NULL, until_min INTEGER NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, latency INTEGER);
            CREATE TABLE IF NOT EXISTS provider_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=1;
            ''')
        with self.db() as c:
            if 'target' not in [r['name'] for r in c.execute('PRAGMA table_info(outbox)')]:
                c.execute("ALTER TABLE outbox ADD COLUMN target TEXT NOT NULL DEFAULT '{}'")
        self._seed_providers()
        os.chmod(self.path, 0o600)

    def _seed_providers(self):
        """Seed verified provider directory once; subsequent launches preserve edits."""
        seed = [
            {'id':'beget','name':'Beget','website_url':'https://beget.com','panel_url':'https://cp.beget.com','panel_name':'Панель управления Beget','source_url':'https://beget.com/ru/contacts','contacts':[
                {'type':'phone','label':'Россия, бесплатно','value':'8 (800) 700-06-08'}, {'type':'phone','label':'Москва','value':'+7 (495) 721-80-88'}, {'type':'phone','label':'Санкт-Петербург','value':'+7 (812) 385-41-36'}, {'type':'email','label':'Техническая поддержка','value':'support@beget.com'}, {'type':'ticket','label':'Тикет в панели','value':'Открыть поддержку','url':'https://cp.beget.com/support'}, {'type':'telegram','label':'Telegram-бот техподдержки','value':'@begetbot','url':'https://t.me/begetbot'}, {'type':'news','label':'Официальный новостной канал Beget','value':'@beget_official','url':'https://t.me/beget_official'}, {'type':'status','label':'Telegram-бот статуса Beget (ссылка указана пользователем)','value':'@BegetStatus_Bot','url':'https://t.me/BegetStatus_Bot'}, {'type':'feedback','label':'Отзывы о работе сотрудников / жалоба на оператора','value':'feedback@beget.com'}, {'type':'abuse','label':'Обработка жалоб на нарушения (abuse)','value':'abuse@beget.com'}, {'type':'complaint','label':'Претензии по услугам (e-mail для дублирования письменной претензии)','value':'abuse@beget.com'}]},
            {'id':'firstvds','name':'FirstVDS','website_url':'https://firstvds.ru','panel_url':'https://my.firstvds.ru','panel_name':'Личный кабинет FirstVDS · VMmanager','source_url':'https://firstvds.ru/company/contacts','contacts':[
                {'type':'phone','label':'Россия, круглосуточно','value':'8 800 775-38-37'}, {'type':'phone','label':'Международный','value':'+7 495 663-73-72'}, {'type':'ticket','label':'Поддержка в личном кабинете','value':'Войти в кабинет','url':'https://my.firstvds.ru'}, {'type':'chat','label':'Онлайн-чат','value':'FirstVDS','url':'https://firstvds.ru'}]},
            {'id':'timeweb-cloud','name':'Timeweb Cloud','website_url':'https://timeweb.cloud','panel_url':'https://timeweb.cloud/my/login','panel_name':'Панель управления Timeweb Cloud','source_url':'https://timeweb.cloud/docs/account-management/contact-support','contacts':[
                {'type':'phone','label':'Россия, ежедневно 08:00–20:00 МСК','value':'8 800 700-32-92'}, {'type':'phone','label':'Санкт-Петербург, ежедневно 08:00–20:00 МСК','value':'8 812 470-71-92'}, {'type':'email','label':'Техническая поддержка, круглосуточно','value':'info@timeweb.cloud'}, {'type':'ticket','label':'Тикет в панели, круглосуточно','value':'Поддержка → Центр поддержки','url':'https://timeweb.cloud/my/login'}, {'type':'chat','label':'Онлайн-чат, круглосуточно','value':'Чат Timeweb Cloud','url':'https://timeweb.cloud'}, {'type':'telegram','label':'Telegram сообщества','value':'@twcloud_chat','url':'https://t.me/twcloud_chat'}]},
            {'id':'timeweb','name':'Timeweb','website_url':'https://timeweb.com','panel_url':'https://hosting.timeweb.ru','panel_name':'Панель управления хостингом Timeweb','source_url':'https://timeweb.com/ru/contacts/','contacts':[
                {'type':'phone','label':'Россия','value':'8 (800) 700-10-81'}, {'type':'email','label':'Поддержка клиентов','value':'info@timeweb.ru'}, {'type':'ticket','label':'Тикеты в панели','value':'Поддержка → Написать в поддержку','url':'https://hosting.timeweb.ru'}, {'type':'chat','label':'Онлайн-чат','value':'Чат Timeweb','url':'https://timeweb.com'}]}
            ,{'id':'sweb','name':'Sweb','website_url':'https://sweb.ru','panel_url':'https://mcp.sweb.ru/main/auth/','panel_name':'Панель управления SpaceWeb','source_url':'https://sweb.ru/contactinfo/','contacts':[
                {'type':'phone','label':'Россия, круглосуточно','value':'8 (800) 777-86-49'}, {'type':'phone','label':'Москва','value':'+7 (495) 109-41-49'}, {'type':'phone','label':'Санкт-Петербург','value':'+7 (812) 209-41-49'}, {'type':'email','label':'Техническая поддержка, круглосуточно','value':'support@sweb.ru'}, {'type':'ticket','label':'Заявка в панели → Поддержка','value':'Войти в панель','url':'https://mcp.sweb.ru/main/auth/'}]}
            ,{'id':'nic-ru','name':'NIC.RU','website_url':'https://www.nic.ru','panel_url':'https://www.nic.ru/hcp/','panel_name':'Панель управления хостингом RU-CENTER','source_url':'https://www.nic.ru/help/monitoring-domenov_4451.html','notes':'Официальные телефоны и панель подтверждены страницами NIC.RU. Поддержка также доступна через кабинет. Адрес поддержки по электронной почте не добавлен: не удалось подтвердить его на официальном источнике.','contacts':[
                {'type':'phone','label':'Служба поддержки RU-CENTER','value':'+7 (495) 009-13-33'}, {'type':'phone','label':'Служба поддержки RU-CENTER','value':'+7 (495) 994-46-01'}, {'type':'ticket','label':'Поддержка в личном кабинете','value':'Открыть поддержку','url':'https://www.nic.ru/support/'}, {'type':'news','label':'Официальный Telegram-канал Руцентра','value':'@rucenter','url':'https://t.me/rucenter'}]}
        ]
        with self.db() as c:
            now = minute()
            resolved_ids = {}
            for item in seed:
                cfg = validate_provider(item)
                old = c.execute('SELECT * FROM providers WHERE id=?', (item['id'],)).fetchone()
                if not old:
                    old = c.execute("SELECT * FROM providers WHERE lower(json_extract(config, '$.name'))=lower(?)", (item['name'],)).fetchone()
                    if not old:
                        c.execute('INSERT INTO providers VALUES (?,?,?,?,?)', (item['id'], 1, now, now, json.dumps(cfg, ensure_ascii=False)))
                        resolved_ids[item['id']] = item['id']
                    else:
                        resolved_ids[item['id']] = old['id']
                else:
                    resolved_ids[item['id']] = old['id']
            # One-time additive migration: add new official/user-supplied contact categories
            # without overwriting the provider name, links, notes, or existing contact edits.
            seeded = c.execute("SELECT value FROM provider_meta WHERE key='contact_categories_v2'").fetchone()
            if not seeded:
                for item in seed:
                    old = c.execute('SELECT * FROM providers WHERE id=?', (resolved_ids.get(item['id'], item['id']),)).fetchone()
                    if not old: continue
                    current = json.loads(old['config'])
                    contacts = current.setdefault('contacts', [])
                    existing = {(x.get('type'), x.get('value')) for x in contacts}
                    additions = [x for x in item['contacts'] if (x['type'], x['value']) not in existing]
                    if additions:
                        current['contacts'] = contacts + additions
                        c.execute('UPDATE providers SET rev=?,updated=?,config=? WHERE id=?',
                                  (old['rev'] + 1, now, json.dumps(current, ensure_ascii=False), old['id']))
                c.execute("INSERT INTO provider_meta VALUES ('contact_categories_v2','1')")
            # Correct the previously seeded Timeweb Cloud login address while
            # preserving any panel URL the user has already customized.
            panel_migration = c.execute("SELECT value FROM provider_meta WHERE key='timeweb_cloud_login_panel_v1'").fetchone()
            if not panel_migration:
                provider_id = resolved_ids.get('timeweb-cloud', 'timeweb-cloud')
                row = c.execute('SELECT * FROM providers WHERE id=?', (provider_id,)).fetchone()
                if row:
                    current = json.loads(row['config'])
                    changed = False
                    if current.get('panel_url') == 'https://timeweb.cloud/my':
                        current['panel_url'] = 'https://timeweb.cloud/my/login'
                        changed = True
                    for contact in current.get('contacts', []):
                        if contact.get('url') == 'https://timeweb.cloud/my':
                            contact['url'] = 'https://timeweb.cloud/my/login'
                            changed = True
                    if changed:
                        c.execute('UPDATE providers SET rev=?,updated=?,config=? WHERE id=?',
                                  (row['rev']+1, now, json.dumps(current, ensure_ascii=False), provider_id))
                        c.execute('DELETE FROM provider_diagnostics WHERE provider=?', (provider_id,))
                c.execute("INSERT INTO provider_meta VALUES ('timeweb_cloud_login_panel_v1','1')")
            # Link earlier free-text entries to their matching directory records.
            provider_ids = {json.loads(r['config'])['name'].strip().casefold():r['id'] for r in c.execute('SELECT id,config FROM providers').fetchall()}
            aliases = {'beget':provider_ids.get('beget','beget'), 'firstvds':provider_ids.get('firstvds','firstvds'), 'timeweb':provider_ids.get('timeweb','timeweb'), 'timeweb cloud':provider_ids.get('timeweb cloud','timeweb-cloud'), 'nic.ru':provider_ids.get('nic.ru','nic-ru'), 'nic.ru / ru-center':provider_ids.get('nic.ru','nic-ru'), 'ru-center':provider_ids.get('nic.ru','nic-ru')}
            for row in c.execute('SELECT id,config FROM sites').fetchall():
                cfg = json.loads(row['config'])
                if not cfg.get('hosting_provider_id'):
                    key = str(cfg.get('hoster') or '').strip().casefold()
                    if key in aliases:
                        cfg['hosting_provider_id'] = aliases[key]
                        c.execute('UPDATE sites SET config=? WHERE id=?', (json.dumps(cfg, ensure_ascii=False), row['id']))

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=5000')
        try:
            with c:
                yield c
        finally:
            c.close()

    def settings(self):
        defaults = {'paused': False, 'channels': [], 'email_to': '', 'mail_account_id': '', 'workers': 4}
        with self.db() as c:
            defaults.update({r['key']: json.loads(r['value']) for r in c.execute('SELECT * FROM settings')})
        return defaults

    def save_settings(self, patch):
        cfg = self.settings()
        for k in patch:
            if k not in cfg:
                raise ValueError('Неизвестная настройка ' + k)
        cfg.update(patch)
        if not isinstance(cfg['paused'], bool) or not isinstance(cfg['workers'], int) or not 1 <= cfg['workers'] <= 4:
            raise ValueError('Допустимо 1–4 параллельные проверки')
        if not isinstance(cfg['channels'], list) or any(x not in ('telegram', 'max', 'email', 'simulation') for x in cfg['channels']):
            raise ValueError('Неизвестный канал')
        if not all(isinstance(cfg[k], str) and len(cfg[k]) < 320 for k in ('email_to', 'mail_account_id')):
            raise ValueError('Неверные параметры почты')
        if 'email' in cfg['channels'] and ('@' not in cfg['email_to'] or not cfg['mail_account_id']):
            raise ValueError('Выберите ящик отправителя и получателя')
        old = self.settings()
        with self.db() as c:
            for k, v in cfg.items():
                c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (k, json.dumps(v)))
            if cfg['paused'] != old['paused']:
                for s in c.execute('SELECT id FROM sites').fetchall():
                    self._exclude(c, s['id'], 'global_pause', cfg['paused'], minute())
                c.execute('UPDATE current SET failures=0,successes=0')
                if not cfg['paused']:
                    c.execute('UPDATE sites SET due=?', (minute(),))
            c.execute('INSERT INTO audit(at,action) VALUES (?,?)', (minute(), 'settings_changed'))
        return cfg

    @staticmethod
    def _exclude(c, sid, reason, on, now):
        if on:
            if not c.execute('SELECT 1 FROM exclusions WHERE site=? AND reason=? AND end IS NULL', (sid, reason)).fetchone():
                c.execute('INSERT INTO exclusions(site,start,reason) VALUES (?,?,?)', (sid, now, reason))
        else:
            c.execute('UPDATE exclusions SET end=? WHERE site=? AND reason=? AND end IS NULL', (now, sid, reason))

    def all(self):
        with self.db() as c:
            items = []
            for r in c.execute('SELECT * FROM sites ORDER BY created,id'):
                config = json.loads(r['config'])
                config.setdefault('dns_host', '')
                config.setdefault('console_url', '')
                config.setdefault('check_http', True)
                config.setdefault('check_https', True)
                config.setdefault('hosting_expires', '')
                config.setdefault('domain_expires', '')
                config.setdefault('hoster_url', '')
                config.setdefault('hosting_provider_id', '')
                for key in ('hosting_vault_id','dns_vault_id','dns_provider','dns_provider_url','dns_panel_url','server_name','server_ip','network_operator','registrar','mail_provider','asset_id'):
                    config.setdefault(key, '')
                for key in ('panel_name','dns_panel_name','dns_nameservers','dns_mx_hosts','hosting_candidate','mail_imap_host','mail_smtp_host','mail_mx_host','mail_smtp_kind'):
                    config.setdefault(key, '')
                provider = self.provider(config.get('hosting_provider_id')) if config.get('hosting_provider_id') else None
                if provider:
                    config['hoster'] = provider['name']
                    config['hoster_url'] = provider['website_url']
                    config['hosting_provider'] = provider
                else:
                    config.setdefault('hosting_provider', None)
                items.append(dict(config, id=r['id'], rev=r['rev'], created=r['created'], due=r['due']))
            return items

    def providers(self):
        with self.db() as c:
            items = [dict(json.loads(r['config']), id=r['id'], rev=r['rev'], created=r['created'], updated=r['updated']) for r in c.execute('SELECT * FROM providers ORDER BY created,id')]
            diagnostics = {r['provider']:dict(r) for r in c.execute('SELECT * FROM provider_diagnostics')}
            for item in items:
                result = diagnostics.get(item['id'])
                item['panel_check'] = result if result and result['rev'] == item['rev'] else None
            return items

    def provider(self, provider_id):
        return next((x for x in self.providers() if x['id'] == provider_id), None)

    def save_provider(self, data):
        cfg = validate_provider(data)
        pid = str(data.get('id') or uuid.uuid4().hex)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', pid):
            raise ValueError('Неверный идентификатор провайдера')
        now = minute()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM providers WHERE id=?', (pid,)).fetchone()
            if old and data.get('rev') != old['rev']:
                raise ValueError('Карточка провайдера изменилась. Обновите список перед сохранением')
            if c.execute('SELECT 1 FROM providers WHERE id<>? AND lower(json_extract(config, \'$.name\'))=lower(?)', (pid, cfg['name'])).fetchone():
                raise ValueError('Провайдер с таким названием уже есть')
            rev = old['rev'] + 1 if old else 1
            if old:
                previous = json.loads(old['config'])
                c.execute('UPDATE providers SET rev=?,updated=?,config=? WHERE id=?', (rev, now, json.dumps(cfg, ensure_ascii=False), pid))
                if previous.get('panel_url') != cfg.get('panel_url'):
                    c.execute('DELETE FROM provider_diagnostics WHERE provider=?', (pid,))
            else:
                c.execute('INSERT INTO providers VALUES (?,?,?,?,?)', (pid, rev, now, now, json.dumps(cfg, ensure_ascii=False)))
            c.execute('INSERT INTO audit(at,action) VALUES (?,?)', (now, 'provider_saved'))
        return self.provider(pid)

    def save_provider_diagnostic(self, provider_id, result, now=None, ttl=15, rev=None):
        now = minute() if now is None else now
        with self.db() as c:
            row = c.execute('SELECT rev FROM providers WHERE id=?', (provider_id,)).fetchone()
            if not row or (rev is not None and row['rev'] != rev):
                return
            c.execute('INSERT OR REPLACE INTO provider_diagnostics VALUES (?,?,?,?,?,?,?,?)',
                      (provider_id, row['rev'], result['state'], now, now+ttl,
                       result.get('reason',''), json.dumps(result.get('detail',{}),ensure_ascii=False)[:1024],
                       result.get('latency')))

    def get(self, sid):
        return next((s for s in self.all() if s['id'] == sid), None)

    def save(self, data):
        cfg = validate(data)
        if cfg.get('hosting_provider_id') and not self.provider(cfg['hosting_provider_id']):
            raise ValueError('Выбранный хостинг-провайдер не найден')
        sid = str(data.get('id') or uuid.uuid4().hex)
        now = minute()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM sites WHERE id=?', (sid,)).fetchone()
            if old and data.get('rev') != old['rev']:
                raise ValueError('Карточка изменилась. Обновите её перед сохранением')
            rev = old['rev'] + 1 if old else 1
            if old:
                c.execute('UPDATE sites SET config=?,rev=?,due=? WHERE id=?', (json.dumps(cfg, ensure_ascii=False), rev, now, sid))
                metadata = {'name', 'hoster', 'hoster_url', 'hosting_provider_id', 'panel_name', 'dns_provider', 'dns_provider_url', 'dns_panel_url', 'dns_panel_name', 'dns_nameservers', 'dns_mx_hosts', 'registrar', 'network_operator', 'hosting_candidate', 'panel_url', 'vault_id', 'hosting_vault_id', 'dns_vault_id', 'server_id', 'asset_id', 'server_name', 'server_ip', 'mail_provider', 'mail_imap_host', 'mail_smtp_host', 'mail_mx_host', 'notes', 'hosting_expires', 'domain_expires'}
                previous = json.loads(old['config'])
                changed = any(previous.get(k) != v for k,v in cfg.items() if k not in metadata)
                if changed:
                    c.execute('UPDATE spans SET end=MAX(start,MIN(end,?)) WHERE site=? AND end>?', (now, sid, now))
                    c.execute('DELETE FROM current WHERE site=?', (sid,))
                    # Reconfiguration is not a recovery notification.
                    c.execute('UPDATE incidents SET closed=? WHERE site=? AND closed IS NULL', (now, sid))
            else:
                c.execute('INSERT INTO sites VALUES (?,?,?,?,?)', (sid, rev, now, now, json.dumps(cfg, ensure_ascii=False)))
            self._exclude(c, sid, 'site_pause', not cfg['enabled'], now)
            if self.settings()['paused']:
                self._exclude(c, sid, 'global_pause', True, now)
            c.execute('INSERT INTO audit(at,action,site) VALUES (?,?,?)', (now, 'site_saved', sid))
        return self.get(sid)

    def results(self, sid):
        with self.db() as c:
            current = [dict(r) for r in c.execute('SELECT * FROM current WHERE site=? ORDER BY probe', (sid,))]
            diagnostics = [dict(r) | {'diagnostic': True, 'failures': 0, 'successes': 0} for r in
                           c.execute('SELECT * FROM port_diagnostics WHERE site=?', (sid,))]
            by_probe = {r['probe']: r for r in current}
            for result in diagnostics:
                old = by_probe.get(result['probe'])
                # Scheduled checks remain authoritative when newer; diagnostic results
                # fill in unconfigured services and refresh stale scheduled results.
                if old is None or result['at'] > old['at']:
                    by_probe[result['probe']] = result
            return sorted(by_probe.values(), key=lambda r: r['probe'])

    def save_port_diagnostics(self, sid, results, now=None, ttl=15, rev=None):
        now = minute() if now is None else now
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT rev FROM sites WHERE id=?', (sid,)).fetchone()
            if not row or (rev is not None and row['rev'] != rev):
                return
            for result in results:
                if result.get('probe') not in ('ssh', 'ftp'):
                    continue
                c.execute('INSERT OR REPLACE INTO port_diagnostics VALUES (?,?,?,?,?,?,?,?)',
                          (sid, result['probe'], result['state'], now, now + ttl,
                           result.get('reason', ''), json.dumps(result.get('detail', {}), ensure_ascii=False)[:1024],
                           result.get('latency')))

    def ingest(self, site, results, run_id, now=None):
        now = minute() if now is None else now
        notification_settings = self.settings()
        channels = notification_settings['channels']
        required = ['http'] if site['url'] else (['mail'] if site['mail'] else [])
        if site['health_url'] and site['health_required']:
            required.append('health')
        selected = [r for r in results if r['probe'] in required]
        state = 'DOWN' if any(r['state'] == 'DOWN' for r in selected) else 'UP' if len(selected) == len(required) and all(r['state'] == 'UP' for r in selected) else 'UNKNOWN'
        reason = next((r.get('reason', '') for r in selected if r['state'] != 'UP'), 'OK')
        results = list(results) + [dict(probe='web', state=state, reason=reason, detail={})]
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT rev,config FROM sites WHERE id=?', (site['id'],)).fetchone()
            if not row or row['rev'] != site['rev'] or c.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone():
                return False
            if not json.loads(row['config'])['enabled']:
                return False
            c.execute('INSERT INTO runs VALUES (?,?)', (run_id, now))
            retry = False
            for r in results:
                prev = c.execute('SELECT * FROM current WHERE site=? AND probe=?', (site['id'], r['probe'])).fetchone()
                continuous = prev and prev['until_min'] >= now
                fails = (prev['failures'] if continuous else 0) + 1 if r['state'] == 'DOWN' else 0
                oks = (prev['successes'] if continuous else 0) + 1 if r['state'] == 'UP' else 0
                inc = c.execute('SELECT * FROM incidents WHERE site=? AND probe=? AND closed IS NULL', (site['id'], r['probe'])).fetchone()
                if (fails == 1 or inc and oks == 1):
                    retry = True
            due = now + (1 if retry else site['period'])
            for r in results:
                sid, probe, state = site['id'], r['probe'], r['state']
                prev = c.execute('SELECT * FROM current WHERE site=? AND probe=?', (sid, probe)).fetchone()
                continuous = prev and prev['until_min'] >= now
                fails = (prev['failures'] if continuous else 0) + 1 if state == 'DOWN' else 0
                oks = (prev['successes'] if continuous else 0) + 1 if state == 'UP' else 0
                tail = c.execute('SELECT * FROM spans WHERE site=? AND probe=? ORDER BY id DESC LIMIT 1', (sid, probe)).fetchone()
                if tail and tail['end'] >= now and tail['state'] == state and tail['rev'] == site['rev']:
                    c.execute('UPDATE spans SET end=? WHERE id=?', (due, tail['id']))
                else:
                    if tail and tail['end'] > now:
                        c.execute('UPDATE spans SET end=MAX(start,?) WHERE id=?', (now, tail['id']))
                    c.execute('INSERT INTO spans(site,probe,start,end,state,rev) VALUES (?,?,?,?,?,?)', (sid, probe, now, due, state, site['rev']))
                c.execute('INSERT OR REPLACE INTO current VALUES (?,?,?,?,?,?,?,?,?,?)', (sid, probe, state, now, due, r.get('reason', ''), json.dumps(r.get('detail', {}), ensure_ascii=False)[:4096], r.get('latency'), min(fails, 100), min(oks, 100)))
                if probe not in ('web', 'ssh', 'ftp', 'mail'):
                    continue
                inc = c.execute('SELECT * FROM incidents WHERE site=? AND probe=? AND closed IS NULL', (sid, probe)).fetchone()
                event = None
                if state == 'DOWN' and fails >= 2 and inc is None:
                    iid = uuid.uuid4().hex
                    first = prev['at'] if prev and fails == 2 else now
                    c.execute('INSERT INTO incidents(id,site,probe,start,confirmed,reason) VALUES (?,?,?,?,?,?)', (iid, sid, probe, first, now, r.get('reason', '')))
                    event = 'DOWN'
                elif state == 'UP' and oks >= 2 and inc:
                    iid = inc['id']
                    c.execute('UPDATE incidents SET recovered=?,closed=? WHERE id=?', (prev['at'] if prev else now, now, iid))
                    event = 'UP'
                if event:
                    # ICMP is diagnostic and must not announce a dead server.
                    if probe not in ('web', 'ssh', 'ftp', 'mail'):
                        continue
                    stamp = datetime.fromtimestamp(now*60, ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК')
                    body = f"Dewiar · {site['name']}\n{probe}: {'недоступен' if event == 'DOWN' else 'снова доступен'}\n" + (site['url']+'\n' if site.get('url') else '') + f"Причина: {r.get('reason', '')}\nИнцидент {iid[:8]} · {stamp}"
                    for channel in channels:
                        dedupe = f'{iid}:{event}:{channel}'
                        target = {k: notification_settings[k] for k in ('email_to', 'mail_account_id')}
                        text = body
                        if event == 'UP':
                            pending = c.execute("SELECT id FROM outbox WHERE dedupe=? AND state='pending'", (f'{iid}:DOWN:{channel}',)).fetchone()
                            if pending:
                                c.execute("UPDATE outbox SET state='superseded' WHERE id=?", (pending['id'],))
                                text = body + '\nИнцидент завершился до отправки первого уведомления.'
                        c.execute('INSERT OR IGNORE INTO outbox(id,dedupe,site,channel,body,state,due,target) VALUES (?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, dedupe, sid, channel, text, 'pending', now, json.dumps(target)))
            c.execute('UPDATE sites SET due=? WHERE id=?', (due, site['id']))
            c.execute('DELETE FROM runs WHERE at<?', (now - 10080,))
        return True

    def history(self, site, start, end):
        start = max(start, site['created'])
        end = max(start, end)
        required = ['web']
        with self.db() as c:
            spans = [dict(r) for r in c.execute('SELECT * FROM spans WHERE site=? AND end>? AND start<? ORDER BY start', (site['id'], start, end))]
            exclusions = [dict(r) for r in c.execute('SELECT * FROM exclusions WHERE site=? AND COALESCE(end,?)>? AND start<?', (site['id'], end, start, end))]
        return availability(spans, exclusions, required, start, end)

    def incidents(self, sid=None):
        with self.db() as c:
            if sid:
                return [dict(r) for r in c.execute('SELECT * FROM incidents WHERE site=? ORDER BY confirmed DESC LIMIT 100', (sid,))]
            return [dict(r) for r in c.execute('SELECT * FROM incidents ORDER BY confirmed DESC LIMIT 100')]

    def deliveries(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT id,channel,state,tries,due,error FROM outbox ORDER BY rowid DESC LIMIT 100')]

    def assets(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM server_assets ORDER BY name COLLATE NOCASE,id')]

    def asset(self, asset_id):
        if not asset_id:
            return None
        with self.db() as c:
            row=c.execute('SELECT * FROM server_assets WHERE id=?',(asset_id,)).fetchone()
            return dict(row) if row else None

    def save_asset(self, name, address, provider=''):
        name=str(name or '').strip()[:160]
        address=network_host(str(address or ''),'адрес узла')
        provider=str(provider or '').strip()[:160]
        if not name:
            name='Узел '+address
        now=minute()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM server_assets WHERE address=?',(address,)).fetchone()
            if row:
                c.execute('UPDATE server_assets SET name=?,provider=?,updated=? WHERE id=?',(name,provider,now,row['id']))
                asset_id=row['id']
            else:
                asset_id=uuid.uuid4().hex
                c.execute('INSERT INTO server_assets(id,name,address,provider,created,updated) VALUES(?,?,?,?,?,?)',(asset_id,name,address,provider,now,now))
            c.execute('INSERT INTO audit(at,action,site) VALUES (?,?,?)',(now,'server_asset_saved',None))
            row=c.execute('SELECT * FROM server_assets WHERE id=?',(asset_id,)).fetchone()
            return dict(row)

    def queue_renewal_reminders(self, today=None):
        today = today or datetime.now(ZoneInfo('Europe/Moscow')).date()
        cfg = self.settings()
        if not cfg['channels']:
            return 0
        queued = 0
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            for row in c.execute('SELECT id,config FROM sites').fetchall():
                site = json.loads(row['config'])
                for kind, field, label in (('hosting','hosting_expires','хостинга'),('domain','domain_expires','домена')):
                    if not site.get(field):
                        continue
                    deadline = date.fromisoformat(site[field])
                    days = (deadline-today).days
                    threshold = 0 if days <= 0 else max((n for n in (30,14,7,1) if days <= n), default=None)
                    if threshold is None or c.execute('SELECT 1 FROM renewal_alerts WHERE site=? AND kind=? AND deadline=? AND threshold=?',(row['id'],kind,site[field],threshold)).fetchone():
                        continue
                    if days < 0:
                        message = f"Срок {label} сайта {site['name']} истёк {abs(days)} дн. назад: {site[field]}" + (f"\n{site['url']}" if site.get('url') else '')
                    elif days == 0:
                        message = f"Сегодня заканчивается срок {label} сайта {site['name']}: {site[field]}" + (f"\n{site['url']}" if site.get('url') else '')
                    else:
                        message = f"Через {days} дн. заканчивается срок {label} сайта {site['name']}: {site[field]}" + (f"\n{site['url']}" if site.get('url') else '')
                    alert_id = uuid.uuid4().hex
                    c.execute('INSERT INTO renewal_alerts VALUES (?,?,?,?,?)',(row['id'],kind,site[field],threshold,minute()))
                    target = {k:cfg[k] for k in ('email_to','mail_account_id')}
                    for channel in cfg['channels']:
                        dedupe = f'renewal:{row["id"]}:{kind}:{site[field]}:{threshold}:{channel}'
                        c.execute('INSERT OR IGNORE INTO outbox(id,dedupe,site,channel,body,state,due,target) VALUES (?,?,?,?,?,?,?,?)',(alert_id+channel,dedupe,row['id'],channel,message,'pending',minute(),json.dumps(target)))
                        queued += 1
        return queued


def availability(spans, exclusions, required, start, end):
    points = {start, end}
    for r in spans + exclusions:
        points.update((max(start, min(end, r['start'])), max(start, min(end, r['end'] if r['end'] is not None else end))))
    ordered = sorted(points)
    totals = dict(U=0, D=0, X=0, E=0)
    bars = []
    for a, b in zip(ordered, ordered[1:]):
        if b <= a:
            continue
        if any(r['start'] <= a < (r['end'] if r['end'] is not None else end) for r in exclusions):
            state = 'E'
        else:
            states = [next((r['state'] for r in reversed(spans) if r['probe'] == p and r['start'] <= a < r['end']), 'UNKNOWN') for p in required]
            state = 'D' if 'DOWN' in states else 'U' if states and all(x == 'UP' for x in states) else 'X'
        totals[state] += b - a
        if bars and bars[-1]['state'] == state:
            bars[-1]['end'] = b
        else:
            bars.append(dict(start=a, end=b, state=state))
    known = totals['U'] + totals['D']
    active = known + totals['X']
    return dict(totals, start=start, end=end, uptime=round(totals['U'] * 100 / known, 2) if known else None, coverage=round(known * 100 / active, 2) if active else None, bars=bars)
