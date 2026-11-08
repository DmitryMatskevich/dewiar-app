"""Versioned local handoff and asynchronous receipt of a rendered media file."""
import base64
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from .store import dump, integer, now
from . import video_store as video

SCHEMA = 'dewiar-video-handoff/1'
COMMANDS = {'content_video_preview', 'content_video_handoff', 'content_video_handoffs', 'content_video_import',
            'content_video_import_get', 'content_video_import_list', 'content_video_import_cancel'}
_active = {}
_lock = threading.RLock()


class ImportError(ValueError):
    """Only internally authored messages may be persisted in the journal."""


def migrate(c):
    c.execute('CREATE TABLE content_video_handoffs(hash TEXT PRIMARY KEY,video_id TEXT NOT NULL REFERENCES content_videos(id),video_revision INTEGER NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL)')
    c.execute('CREATE TABLE content_video_imports(id TEXT PRIMARY KEY,video_id TEXT NOT NULL REFERENCES content_videos(id),handoff_hash TEXT NOT NULL REFERENCES content_video_handoffs(hash),request_key TEXT NOT NULL UNIQUE,payload_hash TEXT NOT NULL,state TEXT NOT NULL,deadline REAL NOT NULL,created_at TEXT NOT NULL,receipt TEXT,error TEXT)')


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _handoff(c, digest):
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Укажите корректный hash пакета передачи')
    row = c.execute('SELECT snapshot FROM content_video_handoffs WHERE hash=?', (digest,)).fetchone()
    if not row:
        raise ValueError('Пакет передачи не найден')
    result = json.loads(row['snapshot'])
    if result.get('schema') not in (SCHEMA,'dewiar-video-handoff/2') or _hash(dump(result).encode()) != digest:
        raise ValueError('Пакет передачи повреждён или имеет неподдерживаемую схему')
    return result


def handoff(store, args):
    revision = integer(args.get('revision'), 'Версия видео', 1)
    with store.db() as c:
        row = video._get(c, args.get('id'), revision)
        if row['archived']:
            raise ValueError('Выбрана архивная версия видео')
        if not row['scenes']:
            raise ValueError('Добавьте хотя бы одну сцену перед передачей в монтаж')
        timeline, position = [], 0
        for scene in row['scenes']:
            timeline.append({**scene, 'start_us': position, 'end_us': position + scene['duration_us']})
            position += scene['duration_us']
        source_row = c.execute('SELECT snapshot FROM content_video_sources WHERE video_id=?', (row['id'],)).fetchone()
        payload = {'schema': SCHEMA, 'video_id': row['id'], 'video_revision': revision,
                   'video_sha256': _hash(dump(row).encode()), 'source_ref': row['source_ref'],
                   'storyboard': timeline, 'duration_us': position, 'time_basis': 'planned_microseconds',
                   'status': 'handoff_ready', 'native_project': False, 'media_assets': [],
                   'pending': ['pipeline_selection', 'media_assignment', 'audio_and_captions', 'edit', 'render', 'quality_review']}
        provenance_files={}
        if 'source_records_status' in row:
            from .video_sources import records_in
            from .assets import read
            records=records_in(c,row)
            encoded=dump({'status':row['source_records_status'],'items':records}).encode()
            provenance_files['source-records.json']=encoded
            payload.update(schema='dewiar-video-handoff/2',source_records_sha256=_hash(encoded),source_record_ids=row['source_record_ids'])
            for record in records:
                asset=record['snapshot'].get('asset_id')
                if asset:provenance_files['source-assets/'+asset]=read(store,asset)
            payload['source_assets']={name:_hash(data) for name,data in provenance_files.items() if name.startswith('source-assets/')}
        digest = _hash(dump(payload).encode())
        files = {'handoff.json': dump(payload).encode(), 'video.json': dump(row).encode(),
                 'brief.md': (f"# {row['title']}\n\n{row['brief']}\n\n## Сценарий\n\n{row['script']}\n").encode(),
                 'README.md': ('# Передача видеозадачи в OpenMontage\n\n'
                               'Это пакет исходных материалов Dewiar, не нативный проект OpenMontage.\n'
                               'Следуйте AGENT_GUIDE и выбранному pipeline в вашей установке OpenMontage. '
                               'Не считайте наличие ZIP завершением стадий или разрешением платной генерации/публикации.\n\n'
                               'video.json хранит неизменяемую версию; handoff.json — плановые интервалы [start_us,end_us). '
                               'Это не таймкоды записанной речи. Медиа, голос и монтаж ещё нужно подготовить. '
                               'При импорте результата в Dewiar выберите этот hash:\n\n' + digest + '\n').encode()}
        if source_row:
            files['source.json'] = source_row['snapshot'].encode()
        files.update(provenance_files)
    files['manifest.json'] = dump({'schema': 'dewiar-file-manifest/1', 'handoff_hash': digest,
                                   'files': {name: {'sha256': _hash(data), 'bytes': len(data)} for name, data in files.items()}}).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16; archive.writestr(info, data)
    raw = buffer.getvalue()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Пакет превышает лимит скачивания 8 МиБ')
    with store.db() as c:
        c.execute('INSERT OR IGNORE INTO content_video_handoffs VALUES (?,?,?,?,?)', (digest, row['id'], revision, dump(payload), now()))
    return {'hash': digest, 'video_revision': revision, 'filename': f'video-{row["id"]}-v{revision}.zip',
            'mime': 'application/zip', 'base64': base64.b64encode(raw).decode()}


def handoffs(store, ident):
    with store.db() as c:
        video._get(c, ident)
        return [dict(r) for r in c.execute('SELECT hash,video_revision,created_at FROM content_video_handoffs WHERE video_id=? ORDER BY rowid DESC LIMIT 100', (ident,))]


def _expire(c):
    c.execute("UPDATE content_video_imports SET state='interrupted',error='Проверка прервана или истекло время. Файл не принят.' WHERE state='running' AND deadline<?", (time.time(),))


def get_import(store, ident):
    with store.db() as c:
        _expire(c)
        row = c.execute('SELECT * FROM content_video_imports WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Импорт видео не найден')
        result = dict(row); result.pop('request_key'); result.pop('payload_hash')
        result['receipt'] = json.loads(result['receipt']) if result['receipt'] else None
        hand = _handoff(c, row['handoff_hash'])
        result['video_revision'] = hand['video_revision']
        result['stale'] = video._get(c, row['video_id'])['revision'] != hand['video_revision']
        return result


def imports(store, ident):
    with store.db() as c:
        video._get(c, ident); _expire(c)
        rows = c.execute('SELECT id FROM content_video_imports WHERE video_id=? ORDER BY rowid DESC LIMIT 100', (ident,)).fetchall()
    return [get_import(store, r['id']) for r in rows]


def _run(command, cancel, deadline, output=False):
    # Temp stdout avoids unbounded PIPE accumulation and all stderr stays local.
    with tempfile.TemporaryFile() as stdout:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout if output else subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            while process.poll() is None:
                if cancel.is_set() or time.monotonic() > deadline:
                    raise ImportError('Проверка отменена или истекло время')
                cancel.wait(0.1)
            if process.returncode:
                raise ImportError('Файл не прошёл проверку контейнера или полное декодирование')
            if output:
                stdout.seek(0); data = stdout.read(65537)
                if len(data) > 65536:
                    raise ImportError('Слишком много данных о потоках видео')
                return data
        finally:
            if process.poll() is None:
                process.kill(); process.wait()


def verify_media(path, cancel):
    probe, ffmpeg = shutil.which('ffprobe'), shutil.which('ffmpeg')
    if not probe or not ffmpeg:
        raise ImportError('Для проверки нужны установленные ffprobe и ffmpeg. Автоматическая установка не выполнялась.')
    try:
        result = json.loads(_run([probe, '-v', 'error', '-protocol_whitelist', 'file', '-format_whitelist', 'mov,matroska,webm',
                                  '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,sample_rate,channels',
                                  '-of', 'json', str(path)], cancel, time.monotonic() + 30, output=True))
        duration = Decimal(result['format']['duration'])
        streams = result['streams']
        video_streams = [s for s in streams if s.get('codec_type') == 'video']
        if not duration.is_finite() or not 0 < duration <= 86400 or not video_streams or len(streams) > 32:
            raise ValueError()
        first = video_streams[0]
        if not 1 <= first['width'] <= 8192 or not 1 <= first['height'] <= 8192:
            raise ValueError()
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ImportError('Нужен читаемый видеопоток с длительностью до 24 часов и размером кадра до 8192 px') from exc
    _run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-threads', '2', '-protocol_whitelist', 'file', '-format_whitelist', 'mov,matroska,webm',
          '-i', str(path), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'], cancel, time.monotonic() + 600)
    return {'duration_us': int(duration * 1000000), 'streams': streams, 'full_decode': 'passed',
            'visual_review': 'pending', 'audio_review': 'pending', 'publication': 'not_requested'}


def start_import(store, args, verify=verify_media):
    key = video.text(args.get('request_key'), 'Ключ запроса', 128, True)
    raw = video.text(args.get('path'), 'Локальный путь', 4096, True)
    path = Path(raw).expanduser()
    digest = _hash(dump({'path': str(path), 'handoff_hash': args.get('handoff_hash')}).encode())
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE'); _expire(c)
        old = c.execute('SELECT id,payload_hash FROM content_video_imports WHERE request_key=?', (key,)).fetchone()
        if old:
            if old['payload_hash'] != digest:
                raise ValueError('request_conflict: ключ импорта уже использован')
            return {'id': old['id']}
        hand = _handoff(c, args.get('handoff_hash'))
        if not path.is_absolute() or path.suffix.lower() not in ('.mp4', '.mov', '.webm', '.mkv') or path.is_symlink():
            raise ValueError('Укажите абсолютный путь к обычному MP4/MOV/WebM/MKV файлу')
        try:
            fingerprint = path.stat()
        except OSError as exc:
            raise ValueError('Локальный видеофайл недоступен') from exc
        if not stat.S_ISREG(fingerprint.st_mode) or not 0 < fingerprint.st_size <= 4 * 1024 ** 3:
            raise ValueError('Размер обычного видеофайла должен быть от 1 байта до 4 ГиБ')
        if c.execute("SELECT 1 FROM content_video_imports WHERE state='running'").fetchone():
            raise ValueError('Дождитесь текущего импорта или отмените его')
        ident = uuid.uuid4().hex
        c.execute('INSERT INTO content_video_imports VALUES (?,?,?,?,?,?,?,?,NULL,NULL)',
                  (ident, hand['video_id'], args['handoff_hash'], key, digest, 'running', time.time() + 900, now()))
    cancel = threading.Event()
    def work():
        incoming = None
        try:
            root = store.root / 'renders'; root.mkdir(mode=0o700, exist_ok=True)
            if root.is_symlink(): raise ImportError('Недопустимая ссылка хранилища видео')
            if shutil.disk_usage(root).free < fingerprint.st_size + 64 * 1024 ** 2:
                raise ImportError('Недостаточно свободного места для безопасного импорта')
            fd, incoming = tempfile.mkstemp(prefix='.import-', suffix=path.suffix.lower(), dir=root)
            hasher, size = hashlib.sha256(), 0
            with os.fdopen(fd, 'wb') as out, os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as source:
                before = os.fstat(source.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (fingerprint.st_ino, fingerprint.st_size, fingerprint.st_mtime_ns):
                    raise ImportError('Файл изменился перед импортом; выберите его заново')
                start = time.monotonic()
                while chunk := source.read(1024 * 1024):
                    if cancel.is_set() or time.monotonic() - start > 180: raise ImportError('Копирование отменено или истекло время')
                    size += len(chunk)
                    if size > fingerprint.st_size: raise ImportError('Размер исходного файла изменился')
                    out.write(chunk); hasher.update(chunk)
                after = os.fstat(source.fileno())
                if size != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ImportError('Исходный файл изменился во время чтения')
                out.flush(); os.fsync(out.fileno())
            receipt = verify(Path(incoming), cancel)
            checksum = hasher.hexdigest(); filename = checksum + path.suffix.lower(); target = root / filename
            with store.db() as c:
                c.execute('BEGIN IMMEDIATE'); _expire(c)
                if cancel.is_set() or c.execute('SELECT state FROM content_video_imports WHERE id=?', (ident,)).fetchone()[0] != 'running': return
                if target.is_symlink(): raise ImportError('Недопустимая ссылка сохранённого видео')
                # A previously saved content-addressed file is replaced with this newly verified copy.
                os.replace(incoming, target); incoming = None
                receipt = {**receipt, 'sha256': checksum, 'bytes': size, 'asset_path': 'renders/' + filename,
                           'handoff_hash': args['handoff_hash'], 'video_revision': hand['video_revision'], 'verified_at': now()}
                c.execute("UPDATE content_video_imports SET state='imported',receipt=? WHERE id=? AND state='running'", (dump(receipt), ident))
        except Exception as exc:
            error = str(exc) if isinstance(exc, ImportError) else 'Не удалось принять видеофайл. Проверьте доступность файла и свободное место.'
            with store.db() as c:
                _expire(c); c.execute("UPDATE content_video_imports SET state='failed',error=? WHERE id=? AND state='running'", (error, ident))
        finally:
            if incoming is not None:
                try: os.unlink(incoming)
                except OSError: pass
            with _lock: _active.pop((str(store.path), ident), None)
    thread = threading.Thread(target=work, daemon=True, name='content-video-import')
    with _lock: _active[(str(store.path), ident)] = (cancel, thread)
    try: thread.start()
    except Exception:
        with _lock: _active.pop((str(store.path), ident), None)
        with store.db() as c: c.execute("UPDATE content_video_imports SET state='failed',error='Не удалось запустить импорт' WHERE id=?", (ident,))
        raise
    return {'id': ident}


def cancel_import(store, ident):
    get_import(store, ident)
    with store.db() as c:
        c.execute("UPDATE content_video_imports SET state='canceled' WHERE id=? AND state='running'", (ident,))
    with _lock:
        active = _active.get((str(store.path), ident))
        if active: active[0].set()
    return get_import(store, ident)


def dispatch(store, cmd, args):
    if not isinstance(args, dict): raise ValueError('Некорректные параметры обмена видео')
    if cmd == 'content_video_preview':
        from .video_preview import open_preview
        return {'url': open_preview(store, args.get('id'))}
    if cmd == 'content_video_handoff': return handoff(store, args)
    if cmd == 'content_video_handoffs': return handoffs(store, args.get('id'))
    if cmd == 'content_video_import': return start_import(store, args)
    if cmd == 'content_video_import_get': return get_import(store, args.get('id'))
    if cmd == 'content_video_import_list': return imports(store, args.get('id'))
    if cmd == 'content_video_import_cancel': return cancel_import(store, args.get('id'))
    raise ValueError('Неизвестная команда обмена видео')
