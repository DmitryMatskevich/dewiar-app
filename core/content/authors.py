"""Versioned author catalog; applying a profile copies an immutable credit."""
import hashlib
import json
import uuid
from .store import dump, integer, now

COMMANDS = {'content_author_list', 'content_author_get', 'content_author_create',
            'content_author_update', 'content_author_archive', 'content_author_byline'}
FIELDS = {'name', 'bio', 'site_names'}


def migrate(c):
    c.execute('CREATE TABLE content_authors(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,name TEXT NOT NULL,archived INTEGER NOT NULL,snapshot TEXT NOT NULL)')
    c.execute('CREATE TABLE content_author_revisions(author_id TEXT NOT NULL REFERENCES content_authors(id),revision INTEGER NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(author_id,revision))')
    c.execute('CREATE INDEX content_author_order ON content_authors(archived,name,id)')


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or '\x00' in value or len(value) > limit:
        raise ValueError(f'{label}: допустим текст до {limit} символов')
    value = value.strip()
    if required and not value: raise ValueError(f'{label}: заполните поле')
    return value


def _validate(c, data):
    if not isinstance(data, dict) or set(data) - FIELDS: raise ValueError('Некорректные поля автора')
    result = {'name': '', 'bio': '', 'site_names': {}, **data}
    result['name'] = _text(result['name'], 'Имя автора', 240, True)
    result['bio'] = _text(result['bio'], 'Биография', 4000)
    names = result['site_names']
    if not isinstance(names, dict) or len(names) > 100: raise ValueError('Некорректные подписи сайтов')
    result['site_names'] = {}
    for site, name in names.items():
        if not isinstance(site, str) or not c.execute('SELECT 1 FROM site_profiles WHERE id=?', (site,)).fetchone():
            raise ValueError('Неизвестный сайт подписи')
        result['site_names'][site] = _text(name, 'Подпись сайта', 240, True)
    return result


def _get(c, ident):
    if not isinstance(ident, str): raise ValueError('Укажите автора')
    row = c.execute('SELECT snapshot FROM content_authors WHERE id=?', (ident,)).fetchone()
    if not row: raise ValueError('Автор не найден')
    return json.loads(row['snapshot'])


def _record(c, row):
    encoded = dump(row)
    c.execute('INSERT INTO content_authors VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,name=excluded.name,archived=excluded.archived,snapshot=excluded.snapshot',
              (row['id'], row['revision'], row['name'], row['archived'], encoded))
    c.execute('INSERT INTO content_author_revisions VALUES (?,?,?)', (row['id'], row['revision'], encoded))


def create(store, data, request_key):
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128: raise ValueError('Нужен ключ создания автора')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        data = _validate(c, data)
        digest = hashlib.sha256(dump(data).encode()).hexdigest()
        previous = c.execute('SELECT * FROM request_keys WHERE key=?', (request_key,)).fetchone()
        if previous:
            if previous['command'] != 'author_create' or previous['payload_hash'] != digest:
                raise ValueError('request_conflict: ключ использован с другими данными')
            return json.loads(previous['result'])
        row = {'id': uuid.uuid4().hex, 'revision': 1, 'archived': False, 'created_at': now(), 'updated_at': now(), **data}
        _record(c, row)
        c.execute('INSERT INTO request_keys VALUES (?,?,?,?)', (request_key, 'author_create', digest, dump(row)))
        return row


def update(store, ident, expected, patch, archived=None):
    integer(expected, 'Версия автора', 1)
    if not isinstance(patch, dict) or set(patch) - FIELDS: raise ValueError('Некорректные поля автора')
    if archived is not None and type(archived) is not bool: raise ValueError('Архив должен быть переключателем')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = _get(c, ident)
        if row['revision'] != expected: raise ValueError('revision_conflict: автор изменён. Ваш ввод сохранён в форме; загрузите актуальную карточку.')
        if row['archived'] and patch: raise ValueError('Сначала восстановите автора из архива')
        values = _validate(c, {**{k: row[k] for k in FIELDS}, **patch})
        result = {**row, **values, 'archived': row['archived'] if archived is None else archived}
        if result == row: return row
        result.update(revision=row['revision'] + 1, updated_at=now())
        _record(c, result)
        return result


def listing(store, query='', archived=False, offset=0, limit=50):
    query = _text(query, 'Поиск', 300)
    if type(archived) is not bool: raise ValueError('Архив должен быть переключателем')
    integer(offset, 'Смещение', 0); integer(limit, 'Размер страницы', 1, 100)
    with store.db() as c:
        c.create_function('casefold', 1, lambda v: v.casefold())
        where = 'archived=? AND instr(casefold(name),?)>0'
        args = (archived, query.casefold())
        total = c.execute('SELECT count(*) FROM content_authors WHERE ' + where, args).fetchone()[0]
        items = [dict(r) for r in c.execute('SELECT id,name,revision,archived FROM content_authors WHERE ' + where + ' ORDER BY casefold(name),id LIMIT ? OFFSET ?', (*args, limit, offset))]
        for item in items: item['archived'] = bool(item['archived'])
        return {'items': items, 'total': total, 'offset': offset}


def byline(store, ident, revision, site_id=None):
    integer(revision, 'Версия автора', 1)
    with store.db() as c:
        row = _get(c, ident)
        if row['revision'] != revision: raise ValueError('revision_conflict: профиль автора изменился. Обновите список и выберите подпись заново.')
        if row['archived']: raise ValueError('Автор в архиве')
        if site_id is not None and (not isinstance(site_id, str) or not c.execute('SELECT 1 FROM site_profiles WHERE id=?', (site_id,)).fetchone()):
            raise ValueError('Неизвестный сайт')
        from .author_credit import snapshot
        credit=snapshot(row,site_id)
        return {'id': ident, 'revision': revision, 'site_id': site_id, 'byline': credit['byline'],'author_credit':credit}


def dispatch(store, cmd, args):
    if not isinstance(args, dict): raise ValueError('Некорректные параметры автора')
    allowed = {'content_author_list': {'query', 'archived', 'offset', 'limit'}, 'content_author_get': {'id'},
               'content_author_create': {'data', 'request_key'}, 'content_author_update': {'id', 'expected_revision', 'patch'},
               'content_author_archive': {'id', 'expected_revision', 'archived'}, 'content_author_byline': {'id', 'revision', 'site_id'}}
    if cmd not in allowed or set(args) - allowed[cmd]: raise ValueError('Некорректные поля команды автора')
    if cmd == 'content_author_list': return listing(store, **args)
    if cmd == 'content_author_get':
        with store.db() as c: return _get(c, args.get('id'))
    if cmd == 'content_author_create': return create(store, args.get('data'), args.get('request_key'))
    if cmd == 'content_author_update': return update(store, args.get('id'), args.get('expected_revision'), args.get('patch'))
    if cmd == 'content_author_archive':
        if type(args.get('archived')) is not bool: raise ValueError('Укажите состояние архива')
        return update(store, args.get('id'), args.get('expected_revision'), {}, args['archived'])
    return byline(store, args.get('id'), args.get('revision'), args.get('site_id'))
