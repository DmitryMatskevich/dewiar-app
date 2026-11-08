"""Revision-bound editorial review, separate from technical decode receipts."""
import hashlib
import json
from .store import dump,integer,now
from . import video_assets,video_store

CHECKS={'content','visual','audio','rights','captions'}
COMMANDS={'content_video_review_get','content_video_review_save','content_video_review_history'}

def migrate(c):
    c.execute('CREATE TABLE content_video_reviews(import_id TEXT PRIMARY KEY REFERENCES content_video_imports(id),revision INTEGER NOT NULL,snapshot TEXT NOT NULL)')
    c.execute('CREATE TABLE content_video_review_revisions(import_id TEXT NOT NULL REFERENCES content_video_imports(id),revision INTEGER NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(import_id,revision))')

def _source(c,ident):
    if not isinstance(ident,str):raise ValueError('Укажите принятый файл')
    job=c.execute('SELECT * FROM content_video_imports WHERE id=?',(ident,)).fetchone()
    if not job or job['state']!='imported':raise ValueError('Нужен принятый файл видео')
    media=json.loads(job['receipt']);video=video_store._get(c,job['video_id'])
    from .video_exchange import _handoff
    hand=_handoff(c,job['handoff_hash'])
    return media,video,hand['video_revision']

def _caption(c,ident,lang):
    if lang is None:return None
    if lang not in ('ru','en'):raise ValueError('Выберите язык субтитров или явно «без субтитров»')
    row=c.execute('SELECT snapshot FROM content_captions WHERE import_id=? AND language=?',(ident,lang)).fetchone()
    if not row:raise ValueError('Субтитры выбранного языка не сохранены')
    row=json.loads(row[0])
    if row['archived']:raise ValueError('Выбранные субтитры в архиве')
    return {'id':row['id'],'language':lang,'revision':row['revision'],'sha256':hashlib.sha256(dump(row).encode()).hexdigest(),'asset_sha256':row['asset_sha256']}

def get(store,ident):
    with store.db() as c:
        return get_in(store,c,ident)

def get_in(store,c,ident):
    media,video,source_revision=_source(c,ident)
    raw=c.execute('SELECT snapshot FROM content_video_reviews WHERE import_id=?',(ident,)).fetchone()
    row=json.loads(raw[0]) if raw else None
    reasons=[]
    if video['archived']:reasons.append('Видео в архиве')
    if video['revision']!=source_revision:reasons.append('Сценарий видео изменён после подготовки этого файла')
    if row:
        if media['sha256']!=row['asset_sha256']:reasons.append('Изменён файл результата')
        caption=row['caption_ref']
        if caption:
            try:current=_caption(c,ident,caption['language'])
            except ValueError:current=None
            if current!=caption:reasons.append('Выбранные субтитры изменены или архивированы')
        if row.get('asset_fingerprint'):
            try:stamp=video_assets.fingerprint(video_assets.path_for(store,media).stat())
            except (ValueError,OSError):stamp=None
            if stamp!=row['asset_fingerprint']:reasons.append('Сохранённый файл изменился или недоступен')
    return {'review':row,'video_revision':source_revision,'current_video_revision':video['revision'],'stale_reasons':reasons,
            'accepted':bool(row and row['decision']=='accepted' and not reasons),'media':{'sha256':media['sha256'],'duration_us':media['duration_us'],'full_decode':media.get('full_decode')},
            'evidence_kind':'editor_declared','publication_authorized':False}

def save(store,args):
    expected=integer(args.get('expected_revision'),'Версия проверки',0)
    video_revision=integer(args.get('video_revision'),'Версия видео',1)
    reviewer=video_store.text(args.get('reviewer'),'Редактор',240,True);note=video_store.text(args.get('note',''),'Примечание',4000)
    decision=args.get('decision');checks=args.get('checks');lang=args.get('caption_language');caption_revision=args.get('caption_revision')
    if 'caption_language' not in args:raise ValueError('Явно выберите субтитры или выпуск без отдельной дорожки')
    if decision not in ('draft','accepted','rejected','withdrawn'):raise ValueError('Неизвестное решение проверки')
    if not isinstance(checks,dict) or set(checks)!=CHECKS or any(type(v) is not bool for v in checks.values()):raise ValueError('Заполните все пункты проверки')
    if decision=='accepted' and not all(checks.values()):raise ValueError('Для принятия подтвердите каждый пункт просмотра')
    if decision in ('rejected','withdrawn') and not note.strip():raise ValueError('Укажите причину решения')
    if lang is None and caption_revision is not None:raise ValueError('Версия субтитров без языка недопустима')
    key=video_store.text(args.get('request_key'),'Ключ запроса',128,True)
    digest=hashlib.sha256(dump({k:v for k,v in args.items() if k!='request_key'}).encode()).hexdigest()
    with store.db() as c:
        previous=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if previous:
            if previous['command']!='video_review_save' or previous['payload_hash']!=digest:raise ValueError('request_conflict: ключ использован с другим решением')
            return json.loads(previous['result'])
        media,_,_=_source(c,args.get('import_id'))
    stamp=None
    if decision=='accepted':
        if media.get('full_decode')!='passed':raise ValueError('Нет полного технического декодирования')
        _,_,stamp=video_assets.verify(store,media)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        # Another thread may have committed the same request during checksum I/O.
        previous=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if previous:
            if previous['command']!='video_review_save' or previous['payload_hash']!=digest:raise ValueError('request_conflict: ключ использован с другим решением')
            return json.loads(previous['result'])
        current_media,video,source_revision=_source(c,args.get('import_id'))
        old=c.execute('SELECT revision,snapshot FROM content_video_reviews WHERE import_id=?',(args['import_id'],)).fetchone()
        if (old[0] if old else 0)!=expected:raise ValueError('revision_conflict: проверка изменена другим редактором')
        if source_revision!=video_revision:raise ValueError('Выбрана другая версия исходного видео')
        if decision=='accepted':
            if video['revision']!=source_revision or video['archived']:raise ValueError('Нельзя принять файл устаревшего или архивного сценария')
            try:current_stamp=video_assets.fingerprint(video_assets.path_for(store,media).stat())
            except OSError as exc:raise ValueError('Сохранённый файл недоступен') from exc
            if current_media!=media or current_stamp!=stamp:raise ValueError('Видео изменилось во время проверки')
        caption=json.loads(old['snapshot'])['caption_ref'] if decision=='withdrawn' and old else _caption(c,args['import_id'],lang)
        if caption and decision!='withdrawn':
            if caption['revision']!=integer(caption_revision,'Версия субтитров',1) or caption['asset_sha256']!=media['sha256']:raise ValueError('revision_conflict: выбраны другие субтитры')
        if decision=='withdrawn' and not old:raise ValueError('Проверка ещё не сохранена')
        row={'import_id':args['import_id'],'revision':expected+1,'video_id':video['id'],'video_revision':source_revision,'asset_sha256':media['sha256'],
            'asset_fingerprint':stamp,'caption_ref':caption,'reviewer':reviewer,'note':note,'checks':checks,'decision':decision,'evidence_kind':'editor_declared','created_at':now()}
        encoded=dump(row)
        c.execute('INSERT INTO content_video_reviews VALUES (?,?,?) ON CONFLICT(import_id) DO UPDATE SET revision=excluded.revision,snapshot=excluded.snapshot',(args['import_id'],row['revision'],encoded))
        c.execute('INSERT INTO content_video_review_revisions VALUES (?,?,?)',(args['import_id'],row['revision'],encoded))
        c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'video_review_save',digest,encoded));return row

def dispatch(store,cmd,args):
    allowed={'content_video_review_get':{'import_id'},'content_video_review_save':{'import_id','expected_revision','video_revision','reviewer','note','checks','caption_language','caption_revision','decision','request_key'},'content_video_review_history':{'import_id','offset','limit'}}
    if not isinstance(args,dict) or cmd not in allowed or set(args)-allowed[cmd]:raise ValueError('Некорректная команда проверки видео')
    if cmd=='content_video_review_get':return get(store,args.get('import_id'))
    if cmd=='content_video_review_save':return save(store,args)
    offset=integer(args.get('offset',0),'Смещение',0);limit=integer(args.get('limit',20),'Размер страницы',1,100)
    with store.db() as c:
        _source(c,args.get('import_id'))
        return [json.loads(r[0]) for r in c.execute('SELECT snapshot FROM content_video_review_revisions WHERE import_id=? ORDER BY revision DESC LIMIT ? OFFSET ?',(args['import_id'],limit,offset))]
