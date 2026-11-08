"""Video briefs and storyboards. No renderer, model or network side effects."""
import hashlib
import json
import re
import uuid
from .store import dump, integer, now

FIELDS = {'kind', 'title', 'author', 'site_id', 'language', 'aspect', 'brief', 'script', 'scenes', 'author_credit'}
COMMANDS = {'content_video_list', 'content_video_get', 'content_video_create',
            'content_video_update', 'content_video_archive', 'content_video_revisions',
            'content_video_revision_get', 'content_video_restore', 'content_video_source',
            'content_video_source_records','content_video_source_read','content_video_source_segments'}


def migrate(c):
    c.execute('CREATE TABLE content_videos(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, archived INTEGER NOT NULL, updated_at TEXT NOT NULL, site_id TEXT REFERENCES site_profiles(id), scene_count INTEGER NOT NULL, duration_us INTEGER NOT NULL, snapshot TEXT NOT NULL)')
    c.execute('CREATE TABLE content_video_revisions(id TEXT NOT NULL REFERENCES content_videos(id), revision INTEGER NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(id,revision))')
    c.execute('CREATE TABLE content_video_sources(video_id TEXT PRIMARY KEY REFERENCES content_videos(id), snapshot TEXT NOT NULL)')
    c.execute('CREATE INDEX content_video_order ON content_videos(archived,updated_at DESC,id)')


def text(value, label, limit, required=False):
    if not isinstance(value, str) or '\x00' in value or len(value) > limit:
        raise ValueError(label + ': недопустимый текст или превышен размер')
    if required and not value.strip():
        raise ValueError(label + ': заполните поле')
    return value.strip() if required else value


def validate(c, data,source_ids=()):
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError('Некорректные поля видео')
    value = {'kind': 'video', 'title': '', 'author': '', 'site_id': None,
             'language': 'ru', 'aspect': '16:9', 'brief': '', 'script': '', 'scenes': [], **data}
    if value['kind'] not in ('video', 'reel') or value['aspect'] not in ('16:9', '9:16', '1:1') or value['language'] not in ('ru', 'en'):
        raise ValueError('Неверный тип, язык или формат видео')
    for key, label, limit in [('title', 'Название видео', 240), ('author', 'Автор', 240),
                              ('brief', 'Бриф', 10000), ('script', 'Сценарий', 100000)]:
        value[key] = text(value[key], label, limit, key == 'title')
    site = value['site_id']
    if site is not None and (not isinstance(site, str) or not c.execute('SELECT 1 FROM site_profiles WHERE id=?', (site,)).fetchone()):
        raise ValueError('Неизвестный проект видео')
    scenes = value['scenes']
    if not isinstance(scenes, list) or len(scenes) > 200:
        raise ValueError('Допустимо до 200 сцен')
    clean, seen, total, source_cache = [], set(), 0, {}
    for scene in scenes:
        if not isinstance(scene, dict) or set(scene) - {'id', 'title', 'narration', 'visual_prompt', 'duration_us', 'locked','source_segments'}:
            raise ValueError('Некорректные поля сцены')
        ident = scene.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident) or ident in seen:
            raise ValueError('ID сцен должны быть уникальными')
        seen.add(ident)
        duration = integer(scene.get('duration_us'), 'Длительность сцены', 1, 3600000000)
        total += duration
        locked = scene.get('locked', False)
        if type(locked) is not bool:
            raise ValueError('Защита сцены: нужен переключатель')
        clean.append({'id': ident, 'title': text(scene.get('title', ''), 'Название сцены', 240),
                      'narration': text(scene.get('narration', ''), 'Речь', 10000),
                      'visual_prompt': text(scene.get('visual_prompt', ''), 'Описание кадра', 10000),
                      'duration_us': duration, 'locked': locked})
        if 'source_segments' in scene:
            from .scene_sources import validate as validate_scene_sources
            refs=validate_scene_sources(c,scene['source_segments'],source_ids,source_cache)
            if refs:clean[-1]['source_segments']=refs
    if total > 86400000000 or len(dump(clean).encode()) > 2 * 1024 * 1024:
        raise ValueError('Раскадровка превышает 24 часа или 2 МиБ')
    value['scenes'] = clean
    from .author_credit import validate as validate_credit
    credit=validate_credit(value.pop('author_credit',None),value['author'],c=c)
    if credit is not None and credit['site_id']!=value['site_id']:raise ValueError('Сведения об авторе выбраны для другого проекта')
    if credit is not None:value['author_credit']=credit
    return value


def _get(c, ident, revision=None):
    text(ident, 'ID видео', 128, True)
    if revision is None:
        row = c.execute('SELECT snapshot FROM content_videos WHERE id=?', (ident,)).fetchone()
    else:
        integer(revision, 'Версия видео', 1)
        row = c.execute('SELECT snapshot FROM content_video_revisions WHERE id=? AND revision=?', (ident, revision)).fetchone()
    if not row:
        raise ValueError('Видео или версия не найдены')
    return json.loads(row['snapshot'])


def get(store, ident, revision=None):
    with store.db() as c:
        return _get(c, ident, revision)


def _write(c, row, new=False):
    values = (row['revision'], row['kind'], row['title'], int(row['archived']), row['updated_at'],
              row['site_id'], len(row['scenes']), sum(s['duration_us'] for s in row['scenes']), dump(row), row['id'])
    if new:
        c.execute('INSERT INTO content_videos(revision,kind,title,archived,updated_at,site_id,scene_count,duration_us,snapshot,id) VALUES (?,?,?,?,?,?,?,?,?,?)', values)
    else:
        c.execute('UPDATE content_videos SET revision=?,kind=?,title=?,archived=?,updated_at=?,site_id=?,scene_count=?,duration_us=?,snapshot=? WHERE id=?', values)
    c.execute('INSERT INTO content_video_revisions VALUES (?,?,?)', (row['id'], row['revision'], dump(row)))
    from .video_sources import bind_in
    bind_in(c,row)


def source_snapshot(c, ref):
    if not isinstance(ref, dict) or set(ref) - {'kind', 'id', 'revision', 'scene_ids'} or ref.get('kind') not in ('article', 'video'):
        raise ValueError('Некорректная ссылка на источник видео')
    text(ref.get('id'), 'ID источника', 128, True)
    integer(ref.get('revision'), 'Версия источника', 1)
    if ref['kind'] == 'article':
        if 'scene_ids' in ref:
            raise ValueError('Статья не содержит видеосцен')
        row = c.execute('SELECT snapshot FROM revisions WHERE material_id=? AND revision=?', (ref['id'], ref['revision'])).fetchone()
        if not row:
            raise ValueError('Версия статьи не найдена')
        snapshot = json.loads(row['snapshot'])
    else:
        snapshot = _get(c, ref['id'], ref['revision'])
    reference = {k: ref[k] for k in ('kind', 'id', 'revision')}
    reference.update(title=snapshot['title'], sha256=hashlib.sha256(dump(snapshot).encode()).hexdigest())
    return reference, snapshot


def create(store, args):
    key = text(args.get('request_key'), 'Ключ запроса', 128, True)
    data = args.get('data', {})
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError('Некорректные поля видео')
    ref = args.get('source')
    digest = hashlib.sha256(dump({'data': data, 'source': ref}).encode()).hexdigest()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM request_keys WHERE key=?', (key,)).fetchone()
        if old:
            if old['command'] != 'video_create' or old['payload_hash'] != digest:
                raise ValueError('request_conflict: ключ уже использован')
            return json.loads(old['result'])
        initial, reference, snapshot, warnings = {}, None, None, []
        if ref is not None:
            reference, snapshot = source_snapshot(c, ref)
            initial = {'title': ('Видео: ' + snapshot['title'])[:240], 'author': snapshot['author'], 'site_id': snapshot['site_id']}
            if snapshot.get('author_credit') is not None:initial['author_credit']=snapshot['author_credit']
            if ref['kind'] == 'article':
                if len(snapshot['body']) <= 100000:
                    initial['script'] = snapshot['body']
                else:
                    warnings.append('Исходный текст длиннее 100 000 знаков. Он сохранён полностью как источник; краткий сценарий нужно подготовить отдельно.')
            else:
                selected = ref.get('scene_ids', [s['id'] for s in snapshot['scenes']])
                if not isinstance(selected, list) or len(selected) > 200 or any(not isinstance(s, str) for s in selected) or len(set(selected)) != len(selected):
                    raise ValueError('Некорректный список выбранных сцен')
                existing = {s['id'] for s in snapshot['scenes']}
                if not set(selected) <= existing:
                    raise ValueError('Выбранная сцена отсутствует в версии источника')
                initial.update(kind='reel', aspect='9:16', title=('Рилс: ' + snapshot['title'])[:240], language=snapshot['language'],
                               brief=snapshot['brief'], scenes=[s for s in snapshot['scenes'] if s['id'] in selected])
                initial['script'] = '\n\n'.join(s['narration'] for s in initial['scenes'])
                if len(initial['script']) > 100000:
                    initial['script'] = ''
                    warnings.append('Речь сцен превышает размер сценария. Сцены сохранены; сценарий подготовьте отдельно.')
                reference['scene_ids'] = selected
        if data.get('kind') == 'reel' and 'aspect' not in data:
            initial['aspect'] = '9:16'
        from .author_credit import carry
        from .video_sources import inherit
        provenance=inherit(c,ref,snapshot) if ref is not None else {}
        value = validate(c, carry(initial,data),provenance.get('source_record_ids',()))
        row = {**value, 'id': uuid.uuid4().hex, 'revision': 1, 'archived': False,
               'created_at': now(), 'updated_at': now(), 'source_ref': reference, 'warnings': warnings}
        row.update(provenance)
        _write(c, row, True)
        if snapshot is not None:
            c.execute('INSERT INTO content_video_sources VALUES (?,?)', (row['id'], dump(snapshot)))
        c.execute('INSERT INTO request_keys VALUES (?,?,?,?)', (key, 'video_create', digest, dump(row)))
        return row


def update(store, args, archive=False, restore=False):
    expected = integer(args.get('expected_revision'), 'Версия видео', 1)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = _get(c, args.get('id'))
        if row['revision'] != expected:
            raise ValueError('revision_conflict: видео изменено в другом окне. Ваши правки остаются в форме.')
        patch = args.get('patch', {})
        archived = row['archived']
        if archive:
            archived = args.get('archived')
            if type(archived) is not bool:
                raise ValueError('Архив: нужен переключатель')
            patch = {}
        elif row['archived']:
            raise ValueError('Сначала восстановите видео из архива')
        elif restore:
            previous = _get(c, row['id'], integer(args.get('revision'), 'Версия истории', 1))
            patch = {k: previous.get(k) for k in FIELDS}
        if not isinstance(patch, dict) or set(patch) - FIELDS:
            raise ValueError('Некорректные поля видео')
        if patch.get('kind', row['kind']) != row['kind']:
            raise ValueError('Тип неизменяемый. Создайте отдельный рилс из видео.')
        from .author_credit import carry
        value = validate(c, carry({k:row[k] for k in FIELDS if k in row},patch),row.get('source_record_ids',()))
        if archived == row['archived'] and all(value.get(k) == row.get(k) for k in FIELDS):
            return row
        row.pop('author_credit',None)
        row = {**row, **value, 'archived': archived, 'revision': expected + 1, 'updated_at': now()}
        _write(c, row)
        return row


def listing(store, args):
    offset = integer(args.get('offset', 0), 'Смещение')
    limit = integer(args.get('limit', 50), 'Размер страницы', 1, 100)
    query = text(args.get('query', ''), 'Поиск', 240).casefold()
    kind, archived = args.get('kind', ''), args.get('archived', False)
    if kind not in ('', 'video', 'reel') or type(archived) is not bool:
        raise ValueError('Неверный фильтр')
    where, params = 'archived=?', [int(archived)]
    if kind:
        where += ' AND kind=?'; params.append(kind)
    if query:
        where += ' AND instr(casefold(title),?)>0'; params.append(query)
    with store.db() as c:
        c.create_function('casefold', 1, str.casefold)
        total = c.execute('SELECT count(*) FROM content_videos WHERE ' + where, params).fetchone()[0]
        rows = c.execute('SELECT id,revision,kind,title,archived,updated_at,site_id,scene_count,duration_us FROM content_videos WHERE ' + where + ' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
        return {'items': [{**dict(r), 'archived': bool(r['archived'])} for r in rows], 'total': total}


def revisions(store, args):
    offset = integer(args.get('offset', 0), 'Смещение')
    limit = integer(args.get('limit', 50), 'Размер страницы', 1, 100)
    with store.db() as c:
        _get(c, args.get('id'))
        # Scalar JSON extraction keeps all scripts/storyboards out of the wire response.
        rows = c.execute("SELECT revision,json_extract(snapshot,'$.updated_at') AS updated_at,json_extract(snapshot,'$.title') AS title,json_extract(snapshot,'$.archived') AS archived FROM content_video_revisions WHERE id=? ORDER BY revision DESC LIMIT ? OFFSET ?", (args['id'], limit, offset)).fetchall()
        return [dict(r) for r in rows]


def source(store, args):
    offset = integer(args.get('offset', 0), 'Смещение', 0, 1048576)
    limit = integer(args.get('limit', 20000), 'Размер фрагмента', 1, 20000)
    with store.db() as c:
        row = _get(c, args.get('id'))
        saved = c.execute('SELECT snapshot FROM content_video_sources WHERE video_id=?', (row['id'],)).fetchone()
        if not saved:
            return None
        snapshot = json.loads(saved['snapshot'])
        content = snapshot.get('body', snapshot.get('script', ''))
        table = 'materials' if row['source_ref']['kind'] == 'article' else 'content_videos'
        current = c.execute('SELECT revision FROM ' + table + ' WHERE id=?', (row['source_ref']['id'],)).fetchone()
        return {'ref': row['source_ref'], 'text': content[offset:offset + limit], 'offset': offset,
                'total': len(content), 'current_revision': current['revision'] if current else None,
                'changed': not current or current['revision'] != row['source_ref']['revision']}


def dispatch(store, cmd, args):
    if not isinstance(args, dict):
        raise ValueError('Некорректные параметры видео')
    if cmd == 'content_video_list': return listing(store, args)
    if cmd == 'content_video_get': return get(store, args.get('id'))
    if cmd == 'content_video_create': return create(store, args)
    if cmd == 'content_video_update': return update(store, args)
    if cmd == 'content_video_archive': return update(store, args, archive=True)
    if cmd == 'content_video_revisions': return revisions(store, args)
    if cmd == 'content_video_revision_get': return get(store, args.get('id'), integer(args.get('revision'), 'Версия истории', 1))
    if cmd == 'content_video_restore': return update(store, args, restore=True)
    if cmd == 'content_video_source': return source(store, args)
    if cmd in ('content_video_source_records','content_video_source_read','content_video_source_segments'):
        from .video_sources import dispatch as sources_dispatch
        return sources_dispatch(store,cmd,args)
    raise ValueError('Неизвестная команда видео')
