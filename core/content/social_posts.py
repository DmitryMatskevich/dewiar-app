"""Standalone social-media drafts and browser handoff packages."""
import base64, hashlib, json, os, re, time, uuid
from pathlib import Path
from .store import dump, integer, now
from . import social_store

MAX_FILE=100*1024*1024
CHUNK=512*1024
EXTENSIONS={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.gif':'image/gif','.mp4':'video/mp4','.mov':'video/quicktime','.webm':'video/webm'}
COMMANDS={'content_social_post_list','content_social_post_get','content_social_post_save','content_social_media_upload_start','content_social_media_upload_chunk','content_social_media_upload_finish','content_social_media_upload_cancel','content_social_post_asset_chunk'}

def migrate(c):
    c.execute('CREATE TABLE social_posts(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,archived INTEGER NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
    c.execute('CREATE INDEX social_posts_order ON social_posts(archived,updated_at DESC,id)')
    c.execute('CREATE TABLE social_post_revisions(id TEXT NOT NULL REFERENCES social_posts(id),revision INTEGER NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(id,revision))')

def _safe_name(value):
    if not isinstance(value,str) or not value or len(value)>240 or '\x00' in value:raise ValueError('Некорректное имя файла')
    name=Path(value.replace('\\','/')).name
    if name in ('','.','..') or name!=value.replace('\\','/').split('/')[-1]:raise ValueError('Недопустимое имя файла')
    return name

def _media_root(store):
    p=store.root/'social-media'
    if p.is_symlink():raise ValueError('Папка медиа соцсетей не может быть ссылкой')
    p.mkdir(mode=0o700,exist_ok=True)
    return p

def _upload_root(store):
    p=store.root/'social-uploads'
    if p.is_symlink():raise ValueError('Папка загрузок не может быть ссылкой')
    p.mkdir(mode=0o700,exist_ok=True)
    return p

def _upload_path(store,ident):
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('Некорректная загрузка')
    root=_upload_root(store);return root/(ident+'.part'),root/(ident+'.json')

def _asset_path(store,sha):
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('Некорректное вложение')
    root=_media_root(store);p=root/sha
    if p.is_symlink() or not p.is_file():raise ValueError('Файл вложения не найден')
    return p

def _check_asset(store,item):
    if not isinstance(item,dict) or set(item)!={'sha256','name','mime','size'}:raise ValueError('Некорректное вложение')
    name=_safe_name(item['name']);ext=Path(name).suffix.lower();mime=EXTENSIONS.get(ext)
    if mime is None or item['mime']!=mime:raise ValueError('Поддерживаются изображения JPG, PNG, WebP, GIF и видео MP4, MOV, WebM')
    size=integer(item['size'],'Размер файла',1,MAX_FILE);path=_asset_path(store,item['sha256'])
    if path.stat().st_size!=size:raise ValueError('Размер вложения изменился')
    h=hashlib.sha256()
    with path.open('rb') as f:
        while data:=f.read(1024*1024):h.update(data)
    if h.hexdigest()!=item['sha256']:raise ValueError('Контрольная сумма вложения не совпадает')
    return {'sha256':item['sha256'],'name':name,'mime':mime,'size':size}

def _binding_snapshot(c,binding_id):
    binding=social_store.binding_snapshot(c,binding_id)
    dest=binding['destination'];conn=binding['connection'];identity=binding['identity']
    cap=dest.get('max_characters',0)
    return {'binding':{k:binding['binding'][k] for k in ('id','revision','route','role')},
            'connection':{k:conn[k] for k in ('id','platform','transport','browser') if k in conn},
            'identity':{k:identity[k] for k in ('id','revision','platform','name','kind','external_id','url')},
            'destination':{k:dest.get(k) for k in ('id','revision','platform','name','kind','external_id','url','language','audience','rules','max_characters')}}

def save(store,args):
    allowed={'id','expected_revision','request_key','title','text','binding_ids','attachments','archived'}
    if set(args)-allowed:raise ValueError('Некорректные поля социального материала')
    title=social_store.text(args.get('title',''),'Название',240,True)
    body=social_store.text(args.get('text',''),'Текст поста',10000)
    bindings=args.get('binding_ids',[])
    if not isinstance(bindings,list) or not 1<=len(bindings)<=100 or any(not isinstance(x,str) or not x or len(x)>128 for x in bindings) or len(set(bindings))!=len(bindings):raise ValueError('Выберите от 1 до 100 мест публикации')
    attachments=args.get('attachments',[])
    if not isinstance(attachments,list) or len(attachments)>8:raise ValueError('Можно прикрепить до 8 файлов')
    attachments=[_check_asset(store,a) for a in attachments]
    ident=args.get('id');archived=args.get('archived',False)
    if type(archived) is not bool:raise ValueError('Архив: нужен переключатель')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        targets=[_binding_snapshot(c,b) for b in bindings]
        if any(t['binding']['route']!='manual' for t in targets):raise ValueError('Для материалов соцсетей пока доступен только веб-маршрут вручную')
        for target in targets:
            cap=target['destination'].get('max_characters') or 0
            if cap and len(body)>cap:raise ValueError(f"Текст длиннее лимита {target['destination']['name']}: {len(body)} из {cap} символов")
        if ident:
            if not isinstance(ident,str):raise ValueError('Некорректный ID материала')
            old=c.execute('SELECT * FROM social_posts WHERE id=?',(ident,)).fetchone()
            if not old:raise ValueError('Социальный материал не найден')
            current=json.loads(old['snapshot']);expected=integer(args.get('expected_revision'),'Версия',1)
            if expected!=old['revision']:raise ValueError('revision_conflict: материал изменён; загрузите сохранённую версию')
            row={**current,'title':title,'text':body,'binding_ids':bindings,'targets':targets,'attachments':attachments,'archived':archived,'revision':expected+1,'updated_at':now()}
            if all(current.get(k)==row.get(k) for k in ('title','text','binding_ids','targets','attachments')) and old['archived']==int(archived):return current
            c.execute('UPDATE social_posts SET revision=?,archived=?,snapshot=?,updated_at=? WHERE id=?',(row['revision'],int(archived),dump(row),row['updated_at'],ident))
        else:
            request=social_store.text(args.get('request_key'),'Ключ запроса',128,True)
            old=c.execute('SELECT * FROM request_keys WHERE key=?',(request,)).fetchone()
            payload={'title':title,'text':body,'binding_ids':bindings,'attachments':attachments}
            digest=hashlib.sha256(dump(payload).encode()).hexdigest()
            if old:
                if old['command']!='social_post_create' or old['payload_hash']!=digest:raise ValueError('request_conflict: ключ запроса уже использован')
                return json.loads(old['result'])
            row={'id':uuid.uuid4().hex,'revision':1,'archived':archived,'created_at':now(),'updated_at':now(),'targets':targets,**payload}
            c.execute('INSERT INTO social_posts VALUES (?,?,?,?,?,?)',(row['id'],1,0,dump(row),row['created_at'],row['updated_at']))
            c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(request,'social_post_create',digest,dump(row)))
        c.execute('INSERT INTO social_post_revisions VALUES (?,?,?,?)',(row['id'],row['revision'],dump(row),row['updated_at']))
        return row

def listing(store,args):
    allowed={'query','archived','offset','limit'}
    if set(args)-allowed:raise ValueError('Некорректный фильтр социального материала')
    offset=integer(args.get('offset',0),'Смещение');limit=integer(args.get('limit',50),'Размер страницы',1,100)
    query=social_store.text(args.get('query',''),'Поиск',240).casefold();archived=args.get('archived',False)
    if type(archived) is not bool:raise ValueError('Архив: нужен переключатель')
    with store.db() as c:
        rows=c.execute('SELECT snapshot FROM social_posts WHERE archived=? ORDER BY updated_at DESC,id LIMIT ? OFFSET ?',(int(archived),limit,offset)).fetchall()
        items=[json.loads(r['snapshot']) for r in rows]
        if query:items=[r for r in items if query in r['title'].casefold() or query in r['text'].casefold()]
        total=c.execute('SELECT count(*) FROM social_posts WHERE archived=?',(int(archived),)).fetchone()[0]
        if query:total=sum(query in r['title'].casefold() or query in r['text'].casefold() for r in (json.loads(x[0]) for x in c.execute('SELECT snapshot FROM social_posts WHERE archived=?',(int(archived),))))
        return {'items':[{'id':r['id'],'revision':r['revision'],'title':r['title'],'updated_at':r['updated_at'],'archived':r['archived'],'destination_names':[t['destination']['name'] for t in r['targets']]} for r in items],'total':total}

def get(store,args):
    ident=social_store.text(args.get('id'),'Материал',128,True)
    with store.db() as c:
        row=c.execute('SELECT snapshot FROM social_posts WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Социальный материал не найден')
        return json.loads(row['snapshot'])

def upload_start(store,args):
    if set(args)!={'name','size'}:raise ValueError('Нужны имя и размер файла')
    name=_safe_name(args['name']);size=integer(args['size'],'Размер файла',1,MAX_FILE);mime=EXTENSIONS.get(Path(name).suffix.lower())
    if not mime:raise ValueError('Поддерживаются JPG, PNG, WebP, GIF, MP4, MOV и WebM')
    root=_upload_root(store)
    for stale in root.glob('*.part'):
        if stale.is_symlink():continue
        try:
            if time.time()-stale.stat().st_mtime>86400:
                stale.unlink(missing_ok=True);stale.with_suffix('.json').unlink(missing_ok=True)
        except OSError:pass
    ident=uuid.uuid4().hex;part,meta=_upload_path(store,ident)
    fd=os.open(part,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    meta.write_text(dump({'name':name,'size':size,'mime':mime,'created_at':now()}),encoding='utf-8');os.chmod(meta,0o600)
    return {'upload_id':ident,'chunk_bytes':CHUNK}

def upload_chunk(store,args):
    if set(args)!={'upload_id','offset','b64'}:raise ValueError('Некорректный фрагмент файла')
    part,meta=_upload_path(store,args['upload_id'])
    if not part.is_file() or not meta.is_file() or part.is_symlink() or meta.is_symlink():raise ValueError('Загрузка истекла; начните заново')
    info=json.loads(meta.read_text(encoding='utf-8'));offset=integer(args['offset'],'Смещение',0,MAX_FILE)
    try:data=base64.b64decode(args['b64'],validate=True)
    except Exception as e:raise ValueError('Некорректное кодирование файла') from e
    if not data or len(data)>CHUNK or offset!=part.stat().st_size or offset+len(data)>info['size']:raise ValueError('Фрагмент не совпадает с ожидаемым размером')
    with part.open('ab') as f:f.write(data);f.flush();os.fsync(f.fileno())
    return {'offset':offset+len(data),'size':info['size']}

def upload_finish(store,args):
    if set(args)!={'upload_id'}:raise ValueError('Укажите загрузку')
    part,meta=_upload_path(store,args['upload_id'])
    if not part.is_file() or not meta.is_file() or part.is_symlink() or meta.is_symlink():raise ValueError('Загрузка истекла; начните заново')
    info=json.loads(meta.read_text(encoding='utf-8'));size=part.stat().st_size
    if size!=info['size']:raise ValueError('Файл загружен не полностью')
    h=hashlib.sha256()
    with part.open('rb') as f:
        while data:=f.read(1024*1024):h.update(data)
    sha=h.hexdigest();root=_media_root(store);target=root/sha
    if target.exists():
        if target.is_symlink() or target.stat().st_size!=size:raise ValueError('Повреждён файл с таким ID')
        check=hashlib.sha256()
        with target.open('rb') as f:
            while data:=f.read(1024*1024):check.update(data)
        if check.hexdigest()!=sha:raise ValueError('Контрольная сумма сохранённого файла не совпадает')
        part.unlink()
    else:os.replace(part,target);os.chmod(target,0o600)
    meta.unlink(missing_ok=True)
    return {'sha256':sha,'name':info['name'],'mime':info['mime'],'size':size}

def upload_cancel(store,args):
    if set(args)!={'upload_id'}:raise ValueError('Укажите загрузку')
    part,meta=_upload_path(store,args['upload_id']);part.unlink(missing_ok=True);meta.unlink(missing_ok=True);return {'ok':True}

def asset_chunk(store,args):
    if set(args)!={'sha256','offset'}:raise ValueError('Некорректный запрос файла')
    path=_asset_path(store,args['sha256']);offset=integer(args['offset'],'Смещение',0,MAX_FILE);limit=CHUNK
    with path.open('rb') as f:f.seek(offset);data=f.read(limit+1)
    eof=len(data)<=limit
    if not eof:data=data[:limit]
    return {'b64':base64.b64encode(data).decode(),'offset':offset,'next_offset':offset+len(data),'done':eof,'size':path.stat().st_size}

def dispatch(store,cmd,args):
    if not isinstance(args,dict):raise ValueError('Некорректные параметры соцматериала')
    if cmd=='content_social_post_list':return listing(store,args)
    if cmd=='content_social_post_get':return get(store,args)
    if cmd=='content_social_post_save':return save(store,args)
    if cmd=='content_social_media_upload_start':return upload_start(store,args)
    if cmd=='content_social_media_upload_chunk':return upload_chunk(store,args)
    if cmd=='content_social_media_upload_finish':return upload_finish(store,args)
    if cmd=='content_social_media_upload_cancel':return upload_cancel(store,args)
    if cmd=='content_social_post_asset_chunk':return asset_chunk(store,args)
    raise ValueError('Неизвестная команда соцматериала')
