"""Workspace-scoped text materials with transactional immutable revisions."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from .models import plain_blocks, validate_blocks, body_from_blocks, with_blocks
SCHEMA_VERSION = 24

SITES = [('site-dcnt-ru', 'dcnt.ru', 'DCNT', 'astro'),
         ('site-ockc-ru', 'ockc.ru', 'ОСКС', 'astro'),
         ('site-osk-ru', 'osk.ru', 'OSK', 'unconfigured'),
         ('site-lanmaster-ru', 'lanmaster.ru', 'LANMASTER', 'opencart'),
         ('site-landmaster-ru', 'landmaster.ru', 'LANDMASTER', 'unconfigured')]
FIELDS = {'kind', 'title', 'author', 'body', 'blocks', 'site_id', 'author_credit'}
SUMMARY = 'id,kind,revision,created_at,updated_at,archived,title,author,site_id'


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def integer(value, label, low=0, high=1000000):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label}: недопустимое число')
    return value


class ContentStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'content.db'
        # Create the file with restricted permissions before SQLite writes to it.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self.db() as c:
            version = c.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError('База контента создана новой версией Dewiar')
            c.execute('PRAGMA journal_mode=WAL')
            # Table replacement must disable FK enforcement before BEGIN;
            # foreign_key_check below validates the entire transaction instead.
            if version < 18:
                c.execute('PRAGMA foreign_keys=OFF')
            c.execute('BEGIN IMMEDIATE')
            version = c.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError('База контента создана новой версией Dewiar')
            if 0 < version < SCHEMA_VERSION:
                from .campaign_sources import backup_before_migration
                backup_before_migration(self, version, SCHEMA_VERSION)
            if version == 0:
                statements = [
                    'CREATE TABLE site_profiles(id TEXT PRIMARY KEY, domain TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, adapter TEXT NOT NULL, connection_status TEXT NOT NULL)',
                    "CREATE TABLE materials(id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('article','news')), revision INTEGER NOT NULL CHECK(revision>=1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived INTEGER NOT NULL CHECK(archived IN (0,1)), title TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL, site_id TEXT REFERENCES site_profiles(id))",
                    'CREATE TABLE revisions(material_id TEXT NOT NULL REFERENCES materials(id), revision INTEGER NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(material_id,revision))',
                    'CREATE TABLE request_keys(key TEXT PRIMARY KEY, command TEXT NOT NULL, payload_hash TEXT NOT NULL, result TEXT NOT NULL)',
                    'CREATE INDEX material_order ON materials(archived,updated_at DESC,id)']
                for sql in statements:
                    c.execute(sql)
                c.executemany('INSERT INTO site_profiles VALUES (?,?,?,?,?)', [(*s, 'unverified') for s in SITES])
                c.execute('PRAGMA user_version=1')
            if version < 2:
                c.execute('CREATE TABLE sources(material_id TEXT PRIMARY KEY REFERENCES materials(id), snapshot TEXT NOT NULL)')
                c.execute('PRAGMA user_version=2')
            if version < 3:
                c.execute("ALTER TABLE materials ADD COLUMN blocks TEXT NOT NULL DEFAULT '[]'")
                for row in c.execute('SELECT id,body FROM materials').fetchall():
                    c.execute('UPDATE materials SET blocks=? WHERE id=?', (dump(plain_blocks(row['body'])), row['id']))
                c.execute('PRAGMA user_version=3')
            if version < 4:
                c.execute('CREATE TABLE content_assets(sha256 TEXT PRIMARY KEY, mime TEXT NOT NULL, size INTEGER NOT NULL)')
                c.execute('PRAGMA user_version=4')
            if version < 5:
                c.execute('CREATE TABLE generation_jobs(id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id), base_revision INTEGER NOT NULL, request_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL, input TEXT NOT NULL, output TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL, deadline REAL NOT NULL, error TEXT, applied_revision INTEGER)')
                c.execute('PRAGMA user_version=5')
            if version < 6:
                c.execute('CREATE TABLE editorial_reviews(material_id TEXT NOT NULL REFERENCES materials(id), revision INTEGER NOT NULL, review_version INTEGER NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(material_id,revision))')
                c.execute('PRAGMA user_version=6')
            if version < 7:
                c.execute('CREATE TABLE site_editions(id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id), revision INTEGER NOT NULL, site_id TEXT NOT NULL REFERENCES site_profiles(id), language TEXT NOT NULL, title TEXT NOT NULL, archived INTEGER NOT NULL, snapshot TEXT NOT NULL)')
                c.execute('CREATE TABLE site_edition_revisions(edition_id TEXT NOT NULL REFERENCES site_editions(id), revision INTEGER NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(edition_id,revision))')
                c.execute('CREATE TABLE site_connections(site_id TEXT PRIMARY KEY REFERENCES site_profiles(id), revision INTEGER NOT NULL, local_root TEXT NOT NULL)')
                c.execute('CREATE TABLE site_releases(id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES site_editions(id), edition_revision INTEGER NOT NULL, plan TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT, created_at TEXT NOT NULL)')
                c.execute('PRAGMA user_version=7')
            if version < 8:
                from .social_store import migrate
                migrate(c)
                c.execute('PRAGMA user_version=8')
            if version < 9:
                from .distribution import migrate
                migrate(c)
                c.execute('PRAGMA user_version=9')
            if version < 10:
                from .queue import migrate
                migrate(c)
                c.execute('PRAGMA user_version=10')
            if version < 11:
                from .video_store import migrate
                migrate(c)
                c.execute('PRAGMA user_version=11')
            if version < 12:
                from .video_exchange import migrate
                migrate(c)
                c.execute('PRAGMA user_version=12')
            if version < 13:
                from .higgsfield import migrate
                migrate(c)
                c.execute('PRAGMA user_version=13')
            if version < 14:
                from .authors import migrate
                migrate(c)
                c.execute('PRAGMA user_version=14')
            if version < 15:
                from .manual import migrate
                migrate(c)
                c.execute('PRAGMA user_version=15')
            if version < 16:
                from .captions import migrate
                migrate(c)
                c.execute('PRAGMA user_version=16')
            if version < 17:
                from .video_review import migrate
                migrate(c)
                c.execute('PRAGMA user_version=17')
            if version < 18:
                from .campaign_sources import migrate
                migrate(c)
                if c.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Миграция контента нарушает связи; изменения отменены')
                c.execute('PRAGMA user_version=18')
            if version < 19:
                from .source_records import migrate
                migrate(c)
                if c.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Миграция источников нарушает связи; изменения отменены')
                c.execute('PRAGMA user_version=19')
            if version < 20:
                from .oauth_store import migrate
                migrate(c)
                c.execute('PRAGMA user_version=20')
            if version < 21:
                # Semantic snapshot version: older apps would silently drop
                # portable author credits on edit. Historical bytes stay intact.
                c.execute('PRAGMA user_version=21')
            if version < 22:
                from .video_sources import migrate
                migrate(c)
                c.execute('PRAGMA user_version=22')
            if version < 23:
                # Optional source annotation semantics; retain historical JSON.
                c.execute('PRAGMA user_version=23')
            if version < 24:
                # Older video editors would drop the optional scene citations.
                c.execute('PRAGMA user_version=24')

    @contextmanager
    def db(self):
        from .db_lock import gate
        with gate(self.root):
            c = sqlite3.connect(self.path, timeout=5)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA foreign_keys=ON')
            c.execute('PRAGMA busy_timeout=5000')
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()

    @staticmethod
    def _get(c, mid):
        if not isinstance(mid, str):
            raise ValueError('Укажите ID материала')
        r = c.execute("SELECT m.*,json_extract(r.snapshot,'$.author_credit') AS author_credit FROM materials m JOIN revisions r ON r.material_id=m.id AND r.revision=m.revision WHERE m.id=?", (mid,)).fetchone()
        if not r:
            raise ValueError('Материал не найден')
        row = dict(r)
        row['archived'] = bool(row['archived'])
        row['blocks'] = json.loads(row['blocks'])
        credit=row.pop('author_credit')
        if credit is not None:row['author_credit']=json.loads(credit)
        return row

    def sites(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM site_profiles ORDER BY rowid')]

    def get(self, mid):
        with self.db() as c:
            return self._get(c, mid)

    @staticmethod
    def _validate(c, data):
        if not isinstance(data, dict) or set(data) - FIELDS:
            raise ValueError('Некорректные поля материала')
        result = {'kind': 'article', 'title': '', 'author': '', 'body': '', 'site_id': None, **data}
        result['blocks'] = validate_blocks(data.get('blocks', plain_blocks(result['body'])))
        for block in result['blocks']:
            if block['type']=='unsupported' and not c.execute('SELECT 1 FROM content_assets WHERE sha256=?',(block['original_ref'],)).fetchone():
                raise ValueError('Оригинал HTML не зарегистрирован')
        derived = body_from_blocks(result['blocks'])
        if 'body' in data and 'blocks' in data and data['body'] != derived:
            raise ValueError('body должен совпадать с текстом блоков')
        result['body'] = derived
        if result['kind'] not in ('article', 'news'):
            raise ValueError('Выберите статью или новость')
        for k, limit in [('title', 240), ('author', 240), ('body', 1048576)]:
            value = result[k]
            if not isinstance(value, str) or '\x00' in value:
                raise ValueError(f'{k}: нужен текст без нулевого символа')
            if (len(value.encode('utf-8')) if k == 'body' else len(value)) > limit:
                raise ValueError(f'{k}: превышен допустимый размер')
        result['title'] = result['title'].strip()
        result['author'] = result['author'].strip()
        if not result['title']:
            raise ValueError('Введите название материала')
        if result['site_id'] is not None and (not isinstance(result['site_id'], str) or not c.execute('SELECT 1 FROM site_profiles WHERE id=?', (result['site_id'],)).fetchone()):
            raise ValueError('Неизвестный профиль сайта')
        from .author_credit import validate
        credit=validate(result.pop('author_credit',None),result['author'],c=c)
        if credit is not None and credit['site_id']!=result['site_id']:raise ValueError('Сведения об авторе выбраны для другого сайта')
        if credit is not None:result['author_credit']=credit
        return result

    @staticmethod
    def _record(c, row, source_ids=None):
        c.execute('INSERT INTO revisions VALUES (?,?,?,?)', (row['id'], row['revision'], dump(row), row['updated_at']))
        from .source_records import ids_in, bind_in
        if source_ids is None:
            source_ids = ids_in(c, row['id'], row['revision']-1) if row['revision']>1 else []
        bind_in(c,row['id'],row['revision'],source_ids)

    def create(self, data, request_key, source=None, *, sources=None):
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
            raise ValueError('Нужен ключ запроса создания')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            value = self._validate(c, data)
            if sources is not None and source is not None:raise ValueError('Укажите один способ передачи источников')
            from .source_records import validate_many, put_in
            selected=validate_many(sources if sources is not None else [source] if source is not None else [])
            payload={'material':value,'source':source}
            if sources is not None:payload['sources']=selected
            digest = hashlib.sha256(dump(payload).encode()).hexdigest()
            previous = c.execute('SELECT * FROM request_keys WHERE key=?', (request_key,)).fetchone()
            if previous:
                legacy=False
                if 'blocks' not in json.loads(previous['result']) and value['blocks']==plain_blocks(value['body']):
                    legacy_digest=hashlib.sha256(dump({'material':{k:v for k,v in value.items() if k!='blocks'},'source':source}).encode()).hexdigest()
                    legacy=previous['payload_hash']==legacy_digest
                if (previous['payload_hash'] != digest and not legacy) or previous['command'] != 'create':
                    raise ValueError('request_conflict: ключ уже использован с другими данными')
                return with_blocks(json.loads(previous['result']))
            row = {'id': uuid.uuid4().hex, 'revision': 1, 'created_at': now(), 'updated_at': now(), 'archived': False, **value}
            c.execute('INSERT INTO materials(id,kind,revision,created_at,updated_at,archived,title,author,body,blocks,site_id) VALUES (:id,:kind,:revision,:created_at,:updated_at,:archived,:title,:author,:body,:blocks,:site_id)', {**row,'blocks':dump(row['blocks'])})
            self._record(c, row, [put_in(c,s) for s in selected])
            if source is not None:
                c.execute('INSERT INTO sources VALUES (?,?)', (row['id'],dump(source)))
            c.execute('INSERT INTO request_keys VALUES (?,?,?,?)', (request_key, 'create', digest, dump(row)))
            return row

    def update(self, mid, expected_revision, patch, *, archived=None, restore=None, _c=None, _source_ids=None):
        integer(expected_revision, 'Ревизия', 1)
        if not isinstance(patch, dict) or set(patch) - FIELDS:
            raise ValueError('Некорректные поля изменения')
        if archived is not None and type(archived) is not bool:
            raise ValueError('Архив: нужен переключатель')
        with (self.db() if _c is None else nullcontext(_c)) as c:
            if _c is None:c.execute('BEGIN IMMEDIATE')
            row = self._get(c, mid)
            from .source_records import ids_in
            previous_sources=ids_in(c,mid,row['revision'])
            chosen_sources=previous_sources if _source_ids is None else _source_ids
            if row['revision'] != expected_revision:
                raise ValueError('revision_conflict: материал изменён. Загрузите актуальную версию или сохраните копию.')
            if restore is not None:
                integer(restore, 'Ревизия истории', 1)
                old = c.execute('SELECT snapshot FROM revisions WHERE material_id=? AND revision=?', (mid, restore)).fetchone()
                if not old:
                    raise ValueError('Ревизия не найдена')
                snapshot = with_blocks(json.loads(old['snapshot']))
                patch = {k: snapshot.get(k) for k in FIELDS}
                chosen_sources=ids_in(c,mid,restore)
            from .author_credit import carry
            candidate = carry({k:row[k] for k in FIELDS if k in row},patch)
            if 'blocks' in patch and 'body' not in patch:
                candidate.pop('body')
            elif 'body' in patch and 'blocks' not in patch:
                if len(row['blocks']) != 1 or row['blocks'][0]['type'] != 'paragraph':
                    raise ValueError('Структурированный документ меняется через blocks')
                candidate['blocks'] = [{**row['blocks'][0], 'text':patch['body']}]
            merged = self._validate(c, candidate)
            desired_archived = row['archived'] if archived is None else archived
            if all(row.get(k) == merged.get(k) for k in FIELDS) and row['archived'] == desired_archived and previous_sources==chosen_sources:
                return row
            row.pop('author_credit',None)
            row.update(merged, archived=desired_archived, revision=row['revision'] + 1, updated_at=now())
            c.execute('UPDATE materials SET kind=:kind,revision=:revision,updated_at=:updated_at,archived=:archived,title=:title,author=:author,body=:body,blocks=:blocks,site_id=:site_id WHERE id=:id', {**row,'blocks':dump(row['blocks'])})
            self._record(c, row, chosen_sources)
            return row

    def list(self, query='', kind='', archived=False, offset=0, limit=50):
        integer(offset, 'Смещение'); integer(limit, 'Размер страницы', 1, 100)
        if not isinstance(query, str) or len(query) > 300 or kind not in ('', 'article', 'news') or type(archived) is not bool:
            raise ValueError('Некорректные фильтры')
        where, args = ['archived=?'], [int(archived)]
        if kind:
            where.append('kind=?'); args.append(kind)
        if query.strip():
            # Literal substring; Python casefold gives predictable Cyrillic search.
            where.append('(instr(casefold(title),?)>0 OR instr(casefold(body),?)>0 OR instr(casefold(author),?)>0)')
            args.extend([query.strip().casefold()] * 3)
        with self.db() as c:
            c.create_function('casefold', 1, lambda s: s.casefold())
            predicate = ' AND '.join(where)
            total = c.execute('SELECT COUNT(*) FROM materials WHERE ' + predicate, args).fetchone()[0]
            rows = [dict(x) for x in c.execute('SELECT ' + SUMMARY + ' FROM materials WHERE ' + predicate + ' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', [*args, limit, offset])]
            return {'items': rows, 'total': total}

    def revisions(self, mid, offset=0, limit=50):
        integer(offset, 'Смещение'); integer(limit, 'Размер страницы', 1, 100)
        with self.db() as c:
            self._get(c, mid)
            return [dict(r) for r in c.execute('SELECT revision,created_at FROM revisions WHERE material_id=? ORDER BY revision DESC LIMIT ? OFFSET ?', (mid, limit, offset))]

    def revision(self, mid, revision):
        integer(revision, 'Ревизия', 1)
        with self.db() as c:
            old = c.execute('SELECT snapshot FROM revisions WHERE material_id=? AND revision=?', (mid, revision)).fetchone()
            if not old:
                raise ValueError('Ревизия не найдена')
            return with_blocks(json.loads(old['snapshot']))

    def sources(self, mid, revision=None):
        with self.db() as c:
            row=self._get(c, mid)
            from .source_records import records_in
            return [r['snapshot'] for r in records_in(c,mid,row['revision'] if revision is None else revision)]
