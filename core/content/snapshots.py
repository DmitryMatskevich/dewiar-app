"""Consistent catalog backups and fenced restore. No models or remote actions."""
from contextlib import closing,contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid
from .store import SCHEMA_VERSION,dump,now

DATABASE='workspace/Контент/content.db'
MANIFEST='workspace/Контент/content-backup.json'
MAX_DB=256*1024*1024
FORMAT='dewiar-content-snapshot/1'


def check(c):
    c.execute('PRAGMA trusted_schema=OFF')
    version=c.execute('PRAGMA user_version').fetchone()[0]
    if not 1<=version<=SCHEMA_VERSION:raise ValueError('Версия каталога в копии не поддерживается этим Dewiar')
    if c.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('Каталог в копии повреждён')
    if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Связи каталога в копии повреждены')
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'materials','revisions','site_profiles','request_keys'}<=tables:raise ValueError('В копии нет каталога контента Dewiar')
    return version,tables


def copy_database(source,target,timeout=20,cancel=None):
    deadline=time.monotonic()+timeout
    def progress(_status,_remaining,_total):
        if (cancel is not None and cancel.is_set()) or time.monotonic()>deadline:raise ValueError('Копирование каталога отменено или истекло время ожидания')
    progress(0,0,0)
    source.backup(target,pages=256,progress=progress,sleep=.025)


def pause_restored(c):
    """Only the staged copy is changed. Exact editorial history stays intact."""
    _,tables=check(c);changes={}
    def update(key,sql,args=()):changes[key]=c.execute(sql,args).rowcount
    if 'content_scopes' in tables:update('scopes',"UPDATE content_scopes SET revoked=1 WHERE revoked=0")
    if 'content_outbox' in tables:
        update('outbox',"UPDATE content_outbox SET state=CASE WHEN state IN ('publishing','cancel_requested','verifying','publication_unknown','awaiting_moderation') THEN 'publication_unknown' ELSE 'needs_review' END,owner=NULL,lease_until=NULL,fence=fence+1,cancel_requested=1,error=?,updated=? WHERE state NOT IN ('published','canceled','failed')",('Каталог восстановлен из копии. Проверьте внешнее размещение; автоматическая отправка отключена.',time.time()))
    if 'content_oauth_connections' in tables:
        update('oauth',"UPDATE content_oauth_connections SET state='needs_login',revision=revision+1,credential_ref=NULL,credential_generation=credential_generation+1,metadata='{}',intent_id=NULL,operation=NULL,deadline=NULL,last_error='credential_unavailable',updated_at=? WHERE credential_ref IS NOT NULL OR state IN ('connected','authenticating','verifying')",(now(),))
    if 'generation_jobs' in tables:update('generation',"UPDATE generation_jobs SET state='interrupted',error='Каталог восстановлен из копии; обработка остановлена' WHERE state='running'")
    if 'content_video_imports' in tables:update('video_imports',"UPDATE content_video_imports SET state='interrupted',error='Каталог восстановлен из копии; импорт остановлен' WHERE state='running'")
    if 'content_provider_diagnostics' in tables:update('diagnostics',"UPDATE content_provider_diagnostics SET state='interrupted' WHERE state='running'")
    if 'site_connections' in tables:update('site_connections',"UPDATE site_connections SET local_root='',revision=revision+1 WHERE local_root<>''")
    if 'site_releases' in tables:update('site_previews',"UPDATE site_releases SET state='failed',receipt=? WHERE state IN ('building','preview_built')",(dump({'error':'Каталог восстановлен. Настройте папку сайта и соберите предпросмотр заново.'}),))
    c.commit();check(c)
    return changes


def media(c):
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")};files={}
    if 'content_assets' in tables:
        for sha,size in c.execute('SELECT sha256,size FROM content_assets'):
            if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha) or type(size) is not int or not 0<size<=1048576:raise ValueError('Некорректный файл контента в каталоге')
            files['assets/'+sha]={'sha256':sha,'bytes':size}
    if 'content_video_imports' in tables:
        for (raw,) in c.execute("SELECT receipt FROM content_video_imports WHERE state='imported'"):
            row=json.loads(raw);path=row.get('asset_path','');sha=row.get('sha256','');size=row.get('bytes')
            if not re.fullmatch(r'renders/[a-f0-9]{64}\.(mp4|mov|webm|mkv)',path) or path.split('/')[1].split('.')[0]!=sha or type(size) is not int or not 0<size<=4*1024**3:raise ValueError('Некорректный видеорезультат в каталоге')
            files[path]={'sha256':sha,'bytes':size}
    return files


@contextmanager
def snapshot(path,*,timeout=20,cancel=None):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Каталог контента недоступен или является ссылкой')
    with tempfile.TemporaryDirectory(prefix='dewiar-content-snapshot-') as folder:
        copy=Path(folder)/'content.db';fd=os.open(copy,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(copy)) as target:
            copy_database(source,target,timeout,cancel)
            version,_=check(target);inventory=media(target)
            # Export includes history and current status; authorization is fenced
            # on restore, never by mutating the live source during backup.
            target.execute('PRAGMA journal_mode=DELETE')
        if copy.stat().st_size>MAX_DB:raise ValueError('Каталог больше 256 МиБ: нужен отдельный архив контента')
        report={'format':FORMAT,'schema_version':version,'database_sha256':hashlib.sha256(copy.read_bytes()).hexdigest(),'media':inventory}
        yield copy,report


def related(name):
    normalized=name.replace('\\','/');parts=[x for x in normalized.split('/') if x and x!='.']
    return len(parts)>=2 and parts[:2]==['workspace','Контент']


@contextmanager
def stage(archive):
    """Validate before any current files are changed. Legacy WAL is not guessed."""
    names=archive.namelist()
    if DATABASE not in names:
        if any(related(n) and n.endswith(('content.db','content.db-wal','content.db-shm','content.db-journal','content-backup.json')) for n in names):raise ValueError('В архиве нет однозначного пути к базе контента')
        yield None;return
    if len(names)!=len(set(names)):raise ValueError('В архиве повторяются пути файлов')
    if any(related(n) and ('..' in n.replace('\\','/').split('/') or n!=n.replace('\\','/')) for n in names):raise ValueError('Неоднозначный путь контента в архиве')
    if any(related(n) and n.endswith(('content.db-wal','content.db-journal')) and archive.getinfo(n).file_size for n in names):raise ValueError('Старый архив содержит отдельный WAL/journal. Нужна проверка восстановления в отдельной папке.')
    if archive.getinfo(DATABASE).file_size>MAX_DB:raise ValueError('База в архиве превышает 256 МиБ')
    with tempfile.TemporaryDirectory(prefix='dewiar-content-restore-') as folder:
        path=Path(folder)/'content.db';raw=archive.read(DATABASE)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw)
        report=None
        if MANIFEST in names:
            if archive.getinfo(MANIFEST).file_size>8*1024*1024:raise ValueError('Опись контента слишком большая')
            report=json.loads(archive.read(MANIFEST))
            if not isinstance(report,dict) or report.get('format')!=FORMAT or report.get('database_sha256')!=hashlib.sha256(raw).hexdigest():raise ValueError('Контрольная сумма базы контента не совпадает')
        with closing(sqlite3.connect(path)) as c:
            version,_=check(c);inventory=media(c)
            if report is not None and (report.get('schema_version')!=version or report.get('media')!=inventory):raise ValueError('Опись не соответствует каталогу')
            changes=pause_restored(c);c.execute('PRAGMA journal_mode=DELETE')
        missing=[]
        for rel,item in inventory.items():
            name='workspace/Контент/'+rel
            if name not in names:missing.append(rel);continue
            if archive.getinfo(name).file_size!=item['bytes']:raise ValueError('Размер медиафайла контента не совпадает')
            digest=hashlib.sha256()
            with archive.open(name) as f:
                while data:=f.read(1024*1024):digest.update(data)
            if digest.hexdigest()!=item['sha256']:raise ValueError('Контрольная сумма медиафайла контента не совпадает')
        yield {'path':path,'media':inventory,'missing_media':missing,'paused':changes}


def ensure_idle(path):
    """Called under the same workspace gate as begin_send/finish_send."""
    if not Path(path).exists():return
    if Path(path).is_symlink():raise ValueError('Каталог восстановления не может быть ссылкой')
    with closing(sqlite3.connect(path)) as c:
        _,tables=check(c)
        if 'content_outbox' in tables and c.execute("SELECT 1 FROM content_outbox WHERE state IN ('publishing','cancel_requested') AND owner IS NOT NULL LIMIT 1").fetchone():raise ValueError('Дождитесь завершения текущей отправки перед восстановлением каталога')


def restore_database(staged,path,*,timeout=20):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise ValueError('Каталог восстановления не может быть ссылкой')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
    with closing(sqlite3.connect(staged.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(path)) as target:
        check(source);copy_database(source,target,timeout);check(target)


def write_media(archive,root,inventory):
    """Write only known immutable assets; fail rather than archive wrong bytes."""
    written=[]
    for rel,item in inventory.items():
        path=Path(root)/rel
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():continue
        # General snapshots retain the existing 100 MiB per-file limit. Full
        # render archives are a separate feature; omission is visible in UI.
        if item['bytes']>100*1024*1024:continue
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0));digest=hashlib.sha256();size=0
        with os.fdopen(fd,'rb') as source,archive.open('workspace/Контент/'+rel,'w',force_zip64=True) as target:
            while data:=source.read(1024*1024):
                size+=len(data)
                if size>item['bytes']:raise ValueError('Медиафайл контента изменился во время копирования')
                digest.update(data);target.write(data)
        if size!=item['bytes'] or digest.hexdigest()!=item['sha256']:raise ValueError('Медиафайл контента не совпадает с каталогом')
        written.append(rel)
    return written


def apply(staged,archive,root):
    root=Path(root)
    if root.is_symlink():raise ValueError('Папка контента не может быть ссылкой')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    names=set(archive.namelist());copied=0
    for rel in staged['media']:
        name='workspace/Контент/'+rel
        if name not in names:continue
        dest=root/rel
        if dest.parent.is_symlink() or dest.is_symlink():raise ValueError('Медиа в папке восстановления не может быть ссылкой')
        dest.parent.mkdir(exist_ok=True,mode=0o700)
        tmp=dest.with_name('.restore-'+uuid.uuid4().hex+'.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as out,archive.open(name) as src:
                while data:=src.read(1024*1024):out.write(data)
                out.flush();os.fsync(out.fileno())
            os.replace(tmp,dest);copied+=1
        finally:tmp.unlink(missing_ok=True)
    restore_database(staged['path'],root/'content.db')
    return {'files':copied+1,'omitted_media':len(staged['missing_media']),'paused':staged['paused'],
            'message':'Каталог восстановлен. Автоматические отправки остановлены; подключения требуют проверки.'}


def preflight(staged,root):
    """Reject unsafe destination paths before changing any existing user files."""
    root=Path(root)
    paths=[root,root/'content.db',*(root/p for p in staged['media'])]
    for path in paths:
        if path.is_symlink() or (path.parent!=root.parent and path.parent.is_symlink()):raise ValueError('Путь восстановления контента не может проходить через ссылку')
        if path!=root and path.exists() and not path.is_file():raise ValueError('Файл каталога занят папкой')
