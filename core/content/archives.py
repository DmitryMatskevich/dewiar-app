"""Streaming, local-only content archives. No Engine, models or credentials."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
import zipfile
from . import snapshots as sn
from .db_lock import gate
from .store import dump, now

FORMAT = 'dewiar-content-archive/1'
PREFIX = 'workspace/Контент/'
CHUNK = 1024 * 1024
MAX_FILES = 10000
MAX_TOTAL = 128 * 1024**3
COMMANDS = {'content_archive_start', 'content_archive_jobs', 'content_archive_job', 'content_archive_cancel'}
ACTIVE = {'running', 'cancel_requested'}


class Canceled(ValueError):
    pass


def checkpoint(cancel):
    if cancel.is_set():
        raise Canceled('Операция отменена. Незавершённый результат удалён.')


def local_path(value, directory=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError('Укажите полный локальный путь')
    p = Path(value).expanduser()
    if not p.is_absolute() or any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError('Нужен абсолютный путь без символических ссылок')
    if not (p.is_dir() if directory else p.is_file()):
        raise ValueError('Папка не найдена' if directory else 'Файл архива не найден')
    return p.resolve()


def free_space(folder, size):
    if size > MAX_TOTAL:
        raise ValueError('Архив превышает лимит 128 ГиБ')
    if shutil.disk_usage(folder).free < size + 8 * CHUNK:
        raise ValueError('Недостаточно места: освободите место и повторите операцию')


def stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def transfer(source, target, size, sha, cancel, tick):
    digest = hashlib.sha256(); count = 0
    while True:
        checkpoint(cancel)
        data = source.read(min(CHUNK, size - count + 1))
        if not data:
            break
        count += len(data)
        if count > size:
            raise ValueError('Размер файла не соответствует описи')
        digest.update(data); target.write(data); tick(len(data))
    if count != size or digest.hexdigest() != sha:
        raise ValueError('Контрольная сумма файла не соответствует описи')


def archive_create(store, destination, ident, cancel, update):
    checkpoint(cancel)
    destination = local_path(str(destination), True)
    temporary = destination / ('.dewiar-content-' + ident + '.partial')
    final = destination / ('Dewiar-content-' + ident + '.zip')
    with sn.snapshot(store.path, cancel=cancel) as (database, report):
        inventory = report['media']
        if len(inventory) > MAX_FILES:
            raise ValueError('В архиве допускается не более 10000 медиафайлов')
        total = database.stat().st_size + sum(x['bytes'] for x in inventory.values())
        free_space(destination, total)
        report.update(archive_format=FORMAT, omitted_media=[])
        done = 0
        def tick(size):
            nonlocal done
            done += size; update(phase='copying', done_bytes=done, total_bytes=total)
        files = {sn.DATABASE: (database, database.stat().st_size, report['database_sha256'])}
        for rel, item in inventory.items():
            path = local_path(str(store.root.resolve() / rel))
            if not path.is_relative_to(store.root.resolve()):
                raise ValueError('Файл находится за пределами каталога контента')
            files[PREFIX + rel] = (path, item['bytes'], item['sha256'])
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as output:
                with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for name, (path, size, sha) in files.items():
                        checkpoint(cancel)
                        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as source:
                            before = os.fstat(source.fileno())
                            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                                raise ValueError('Сохранённый файл изменился или недоступен')
                            with archive.open(name, 'w', force_zip64=True) as target:
                                transfer(source, target, size, sha, cancel, tick)
                            if stamp(before) != stamp(os.fstat(source.fileno())):
                                raise ValueError('Файл изменился во время копирования')
                    archive.writestr(sn.MANIFEST, dump(report))
                output.flush(); os.fsync(output.fileno())
            checkpoint(cancel)
            result = {'path': str(final), 'bytes': temporary.stat().st_size, 'media_count': len(inventory),
                      'media_bytes': total - database.stat().st_size, 'schema_version': report['schema_version'],
                      'message': 'Полный архив сохранён. Все файлы описи проверены.'}
            # No overwrite, even if another process creates the same path.
            os.link(temporary, final)
            return result
        finally:
            temporary.unlink(missing_ok=True)


def archive_restore(path, destination, ident, cancel, update):
    checkpoint(cancel)
    path = local_path(str(path)); destination = local_path(str(destination), True)
    temporary = destination / ('.dewiar-restore-' + ident)
    final = destination / ('Dewiar-restored-' + ident)
    if final.exists():
        raise ValueError('Папка результата уже существует')
    # This exclusively owned temporary tree is the only tree cleanup may remove.
    temporary.mkdir(mode=0o700)
    try:
        content = temporary / 'Контент'; content.mkdir(mode=0o700)
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_TOTAL + 16 * CHUNK:
                raise ValueError('Файл архива имеет недопустимый размер')
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist(); names = [e.filename for e in entries]
                if len(names) > MAX_FILES + 2 or len(names) != len(set(names)):
                    raise ValueError('Слишком много файлов или повторяющиеся пути в архиве')
                if any(e.flag_bits & 1 or e.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                       or stat.S_ISLNK(e.external_attr >> 16) for e in entries):
                    raise ValueError('Архив содержит неподдерживаемые или ссылочные записи')
                if sn.MANIFEST not in names or sn.DATABASE not in names:
                    raise ValueError('Нужен полный архив контента Dewiar')
                if archive.getinfo(sn.MANIFEST).file_size > 8 * CHUNK:
                    raise ValueError('Опись архива слишком большая')
                report = json.loads(archive.read(sn.MANIFEST))
                if not isinstance(report, dict) or report.get('format') != sn.FORMAT or report.get('archive_format') != FORMAT or report.get('omitted_media') != []:
                    raise ValueError('Нужен полный архив без пропущенных медиафайлов')
                size = archive.getinfo(sn.DATABASE).file_size
                if not 0 < size <= sn.MAX_DB:
                    raise ValueError('База в архиве превышает 256 МиБ или пуста')
                total = sum(e.file_size for e in entries); free_space(destination, total)
                done = 0
                def tick(n):
                    nonlocal done
                    done += n; update(phase='restoring', done_bytes=done, total_bytes=total)
                database = content / 'content.db'
                fd = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as target, archive.open(sn.DATABASE) as data:
                    transfer(data, target, size, report.get('database_sha256'), cancel, tick)
                with closing(sqlite3.connect(database)) as c:
                    version, _ = sn.check(c); inventory = sn.media(c)
                    if report.get('schema_version') != version or report.get('media') != inventory:
                        raise ValueError('Опись не соответствует базе контента')
                expected = {sn.DATABASE, sn.MANIFEST, *(PREFIX + rel for rel in inventory)}
                if set(names) != expected:
                    raise ValueError('В архиве отсутствует файл описи или присутствуют лишние пути')
                for rel, item in inventory.items():
                    checkpoint(cancel)
                    name = PREFIX + rel
                    if archive.getinfo(name).file_size != item['bytes']:
                        raise ValueError('Размер медиафайла не соответствует описи')
                    dest = content / rel; dest.parent.mkdir(exist_ok=True, mode=0o700)
                    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as target, archive.open(name) as data:
                        transfer(data, target, item['bytes'], item['sha256'], cancel, tick)
                        target.flush(); os.fsync(target.fileno())
                checkpoint(cancel)
                if stamp(before) != stamp(os.fstat(source.fileno())):
                    raise ValueError('Архив изменился во время восстановления')
                with closing(sqlite3.connect(database)) as c:
                    paused = sn.pause_restored(c); c.execute('PRAGMA journal_mode=DELETE')
                with database.open('rb') as db:
                    os.fsync(db.fileno())
        checkpoint(cancel)
        # UUID plus exclusive parent tree: no merge or mutation of live content.
        if final.exists():
            raise ValueError('Папка результата уже существует')
        temporary.rename(final)
        return {'workspace': str(final), 'media_count': len(inventory), 'paused': paused,
                'message': 'Восстановлено в отдельную рабочую папку. Выберите её в настройках рабочей папки для проверки. Подключения требуют повторного входа; автоматические отправки остановлены.'}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


class Service:
    """One worker per catalog, durable small receipts, cross-process fencing."""
    def __init__(self, store):
        self.store = store; self.root = store.root / '.archive-jobs'
        if self.root.is_symlink():
            raise ValueError('Журнал архива не может быть символической ссылкой')
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.RLock(); self.active = None; self.closed = False
        self.recover()

    def read(self, ident):
        if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
            raise ValueError('Некорректная операция архива')
        path = self.root / (ident + '.json')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
            raise ValueError('Операция архива не найдена')
        return json.loads(path.read_text())

    def records(self):
        # A bounded journal; refusing new work at the limit never erases history.
        paths = list(self.root.glob('*.json'))
        if len(paths) > 1000:
            raise ValueError('Журнал архива слишком большой')
        return sorted((self.read(p.stem) for p in paths), key=lambda r: (r['created_at'], r['id']), reverse=True)

    def write(self, row):
        temp = self.root / (row['id'] + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(dump(row)); f.flush(); os.fsync(f.fileno())
        os.replace(temp, self.root / (row['id'] + '.json'))

    def recover(self):
        with self.lock:
            if self.active:
                return
            try:
                with gate(self.root, timeout=.01):
                    for row in self.records():
                        if row['state'] in ACTIVE:
                            try:
                                destination = local_path(row['destination'], True)
                                partial = destination / ('.dewiar-content-' + row['id'] + '.partial')
                                partial.unlink(missing_ok=True)
                                partial_tree = destination / ('.dewiar-restore-' + row['id'])
                                if partial_tree.is_symlink(): partial_tree.unlink()
                                elif partial_tree.is_dir(): shutil.rmtree(partial_tree)
                            except (OSError, ValueError):
                                pass  # A changed/unmounted destination is never guessed.
                            row.update(state='interrupted', message='Предыдущая операция прервана. Проверьте папку назначения перед повтором.'); self.write(row)
            except ValueError as exc:
                if 'занят' not in str(exc):
                    raise

    def start(self, args):
        kind = args.get('kind'); key = args.get('request_key')
        if kind not in ('create', 'restore') or not isinstance(key, str) or not 1 <= len(key) <= 120:
            raise ValueError('Укажите вид операции и ключ запроса')
        destination = str(local_path(args.get('destination'), True))
        source = str(local_path(args.get('path'))) if kind == 'restore' else None
        digest = hashlib.sha256(dump([kind, destination, source]).encode()).hexdigest()
        with self.lock:
            if self.closed:
                raise ValueError('Обработка архивов завершает работу')
            for existing in self.records():
                if existing['request_key'] == key:
                    if existing['payload_hash'] != digest:
                        raise ValueError('Ключ запроса уже использован для другой операции')
                    return existing
            if self.active:
                raise ValueError('Дождитесь текущей операции или отмените её')
            ident = uuid.uuid4().hex; cancel = threading.Event(); ready = threading.Event(); errors = []
            row = {'id': ident, 'kind': kind, 'request_key': key, 'payload_hash': digest, 'created_at': now(),
                   'state': 'running', 'phase': 'preparing', 'done_bytes': 0, 'total_bytes': 0,
                   'destination': destination, 'path': source, 'message': 'Подготовка…', 'result': None}
            def run():
                try:
                    with gate(self.root, timeout=.1):
                        # Another process may have accepted this key first.
                        if any(r['request_key'] == key for r in self.records()):
                            raise ValueError('Этот запрос уже принят другим экземпляром. Обновите журнал.')
                        if len(self.records()) >= 1000:
                            raise ValueError('Достигнут предел 1000 операций в журнале')
                        self.write(row); ready.set(); saved = 0.0
                        def update(**values):
                            nonlocal saved
                            row.update(values)
                            row['message'] = 'Копирование и проверка файлов…' if row['phase']=='copying' else 'Проверка архива и восстановление файлов…'
                            if cancel.is_set(): row['state'] = 'cancel_requested'
                            if time.monotonic() - saved > .3:
                                self.write(row); saved = time.monotonic()
                        try:
                            checkpoint(cancel)
                            if kind == 'create':
                                result = archive_create(self.store, destination, ident, cancel, update)
                            else:
                                result = archive_restore(source, destination, ident, cancel, update)
                            row.update(state='completed', phase='complete', result=result, message=result['message'], done_bytes=row['total_bytes'])
                        except Canceled as exc:
                            row.update(state='canceled', message=str(exc))
                        except (ValueError, OSError, zipfile.BadZipFile, sqlite3.Error, KeyError, TypeError) as exc:
                            text = str(exc) if isinstance(exc, ValueError) else 'Архив не создан или не восстановлен. Проверьте файл, свободное место и права папки.'
                            row.update(state='canceled' if cancel.is_set() else 'failed', message='Операция отменена. Незавершённый результат удалён.' if cancel.is_set() else text)
                        except Exception:
                            row.update(state='failed', message='Не удалось обработать архив. Проверьте его формат и повторите операцию.')
                        self.write(row)
                except Exception as exc:
                    errors.append(str(exc)); ready.set()
                finally:
                    with self.lock:
                        self.active = None
            thread = threading.Thread(target=run, daemon=True, name='content-archive')
            self.active = (ident, cancel, thread); thread.start()
        if not ready.wait(3):
            cancel.set(); raise ValueError('Не удалось запустить операцию архива вовремя')
        if errors:
            raise ValueError(errors[0])
        return self.read(ident)

    def cancel(self, ident):
        with self.lock:
            row = self.read(ident)
            if row['state'] in ACTIVE and self.active and self.active[0] == ident:
                self.active[1].set()
                return {**row, 'state': 'cancel_requested'}
            if row['state'] in ACTIVE:
                raise ValueError('Операция выполняется в другом экземпляре Dewiar')
            return row

    def close(self):
        with self.lock:
            self.closed = True; active = self.active
            if active: active[1].set()
        if active: active[2].join(5)


def command(host, store, cmd, args):
    from interface_origin import require_interface
    require_interface()
    if not isinstance(args, dict):
        raise ValueError('Некорректные параметры архива')
    # Host lock prevents two first requests from creating competing services.
    with _registry_lock:
        services = getattr(host, '_content_archives', None)
        if services is None: services = {}; host._content_archives = services
        key = str(store.root.resolve())
        service = services.get(key)
        if service is None: service = services[key] = Service(store)
    if cmd == 'content_archive_start': return service.start(args)
    if cmd == 'content_archive_cancel': return service.cancel(args.get('id'))
    if cmd == 'content_archive_job': return service.read(args.get('id'))
    service.recover()
    return {'items': service.records()[:20], 'workspace': str(store.root.parent)}


def close(host):
    for service in getattr(host, '_content_archives', {}).values():
        service.close()


_registry_lock = threading.Lock()
