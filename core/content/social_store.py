"""Workspace social catalog. Catalog declarations never grant publishing rights."""
import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qs
from .store import dump, integer, now

PLATFORMS=('linkedin','facebook','threads','x','vk','telegram','pinterest','dzen','max')
TABLES={kind:'social_'+kind for kind in ('connections','identities','destinations','bindings','sets')}
HOSTS={'linkedin':('linkedin.com',),'facebook':('facebook.com','fb.com'),'threads':('threads.com','threads.net'),'x':('x.com','twitter.com'),'vk':('vk.com',),'telegram':('t.me','telegram.me'),'pinterest':('pinterest.com',),'dzen':('dzen.ru',),'max':('max.ru',)}
FIELDS={
 'connections':{'platform':'linkedin','name':'','description':'','transport':'manual'},
 'identities':{'platform':'linkedin','name':'','description':'','kind':'personal','external_id':'','url':'','project_ids':[]},
 'destinations':{'platform':'linkedin','name':'','description':'','kind':'profile','external_id':'','url':'','project_ids':[],'language':'ru','audience':'','rules':''},
 'bindings':{'name':'','description':'','connection_id':'','identity_id':'','destination_id':'','role':'author','route':'manual'},
 'sets':{'name':'','description':'','binding_ids':[],'project_ids':[]}}

def migrate(c):
    for kind,table in TABLES.items():
        refs=', connection_id TEXT NOT NULL REFERENCES social_connections(id), identity_id TEXT NOT NULL REFERENCES social_identities(id), destination_id TEXT NOT NULL REFERENCES social_destinations(id), UNIQUE(connection_id,identity_id,destination_id)' if kind=='bindings' else ''
        c.execute(f'CREATE TABLE {table}(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, platform TEXT NOT NULL, name TEXT NOT NULL, archived INTEGER NOT NULL, snapshot TEXT NOT NULL{refs})')
        c.execute(f'CREATE INDEX {table}_list ON {table}(archived,name,id)')
    c.execute('CREATE TABLE social_set_members(set_id TEXT NOT NULL REFERENCES social_sets(id),binding_id TEXT NOT NULL REFERENCES social_bindings(id),PRIMARY KEY(set_id,binding_id))')
    c.execute('CREATE TABLE social_revisions(kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(kind,id,revision))')

def table(kind):
    if kind not in TABLES:raise ValueError('Неизвестный раздел соцсетей')
    return TABLES[kind]

def text(value,label,limit=240,required=False):
    if not isinstance(value,str) or '\x00' in value or len(value)>limit:raise ValueError(label+': неверный текст или превышен размер')
    value=value.strip()
    if required and not value:raise ValueError('Заполните: '+label)
    return value

def canonical_url(platform,value):
    value=text(value,'Ссылка',2000,True)
    try:u=urlsplit(value);port=u.port
    except ValueError as e:raise ValueError('Некорректная ссылка') from e
    host=(u.hostname or '').lower()
    if u.scheme!='https' or u.username or u.password or port not in (None,443) or not any(host==h or host.endswith('.'+h) for h in HOSTS[platform]):raise ValueError('Нужна HTTPS-ссылка на выбранную соцсеть без логина и пароля')
    query=''
    if platform=='facebook' and u.path=='/profile.php' and re.fullmatch(r'id=\d+',u.query):query=u.query
    if (u.query and not query) or u.fragment:raise ValueError('Используйте постоянную ссылку без параметров и фрагмента')
    if not u.path.strip('/') or re.search(r'[\s\\\x00-\x1f]',value):raise ValueError('Укажите страницу профиля или группы')
    host=host.removeprefix('www.')
    if host=='twitter.com':host='x.com'
    if host=='threads.net':host='threads.com'
    return urlunsplit(('https',host,u.path.rstrip('/'),query,''))

def _get(c,kind,ident):
    if not isinstance(ident,str):raise ValueError('Укажите ID записи соцсетей')
    row=c.execute(f'SELECT snapshot FROM {table(kind)} WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('Запись соцсетей не найдена')
    return json.loads(row['snapshot'])

def get(store,kind,ident):
    with store.db() as c:return _get(c,kind,ident)

def _projects(c,value):
    if not isinstance(value,list) or len(value)>5 or any(not isinstance(v,str) for v in value):raise ValueError('Некорректные проекты')
    result=sorted(set(value));known={r['id'] for r in c.execute('SELECT id FROM site_profiles')}
    if set(result)-known:raise ValueError('Неизвестный проект')
    return result

def _validate(c,kind,data,current=None):
    table(kind)
    if not isinstance(data,dict) or set(data)-FIELDS[kind].keys():raise ValueError('Неизвестные поля соцсетей; секреты и подтверждённые права здесь не сохраняются')
    value={**FIELDS[kind],**data}
    value['name']=text(value['name'],'Название',required=True)
    value['description']=text(value['description'],'Описание',3000)
    if 'project_ids' in value:value['project_ids']=_projects(c,value['project_ids'])
    if 'platform' in value and value['platform'] not in PLATFORMS:raise ValueError('Неизвестная соцсеть')
    if current and 'platform' in value and current['platform']!=value['platform']:raise ValueError('Сеть существующей записи не меняется; создайте новую запись')
    if kind=='connections':
        if value['transport'] not in ('manual','api'):raise ValueError('Неизвестный способ подключения')
        value.update(status='not_connected',credential_ref=None)
    if kind in ('identities','destinations'):
        choices=('personal','company') if kind=='identities' else ('profile','page','group','community')
        if value['kind'] not in choices:raise ValueError('Неизвестный тип профиля или назначения')
        value['url']=canonical_url(value['platform'],value['url']);value['external_id']=text(value['external_id'],'Внешний ID')
        for other in c.execute(f'SELECT id,snapshot FROM {table(kind)} WHERE platform=?',(value['platform'],)):
            obj=json.loads(other['snapshot'])
            if current and obj['id']==current['id']:continue
            if obj['kind']==value['kind'] and (obj['url']==value['url'] or (value['external_id'] and obj['external_id']==value['external_id'])):raise ValueError('Эта запись уже есть в каталоге, включая архив')
        if current and any(current[k]!=value[k] for k in ('url','external_id','kind')):
            ref='identity_id' if kind=='identities' else 'destination_id'
            if c.execute(f'SELECT 1 FROM social_bindings WHERE {ref}=?',(current['id'],)).fetchone():raise ValueError('Адрес и тип используются назначением; создайте новую запись для другого аккаунта')
    if kind=='destinations':
        if value['language'] not in ('ru','en','mixed'):raise ValueError('Неизвестный язык')
        value['audience']=text(value['audience'],'Аудитория',1000);value['rules']=text(value['rules'],'Правила',5000)
    if kind=='bindings':
        related=[_get(c,k,value[f]) for k,f in [('connections','connection_id'),('identities','identity_id'),('destinations','destination_id')]]
        if len({r['platform'] for r in related})!=1:raise ValueError('Подключение, профиль и назначение должны принадлежать одной сети')
        if not current and any(r['archived'] for r in related):raise ValueError('Нельзя добавить назначение с архивным подключением или профилем')
        if current and any(current[k]!=value[k] for k in ('connection_id','identity_id','destination_id')):raise ValueError('Участники существующего назначения не меняются; создайте новое')
        value['platform']=related[0]['platform']
        if value['route'] not in ('manual','api') or value['role'] not in ('author','admin','editor','member'):raise ValueError('Неизвестный маршрут или роль')
        if value['route']=='api' and related[0]['transport']!='api':raise ValueError('Для API нужен отдельный API connection')
        value.update(verification='unverified',capabilities=[],checked_at=None)
        duplicate=c.execute('SELECT id FROM social_bindings WHERE connection_id=? AND identity_id=? AND destination_id=?',tuple(value[k] for k in ('connection_id','identity_id','destination_id'))).fetchone()
        if duplicate and (not current or duplicate['id']!=current['id']):raise ValueError('Такое назначение уже есть, включая архив')
    if kind=='sets':
        members=value['binding_ids']
        if not isinstance(members,list) or len(members)>100 or any(not isinstance(v,str) for v in members):raise ValueError('Набор содержит не более 100 назначений')
        value['binding_ids']=sorted(set(members));value['platform']='mixed'
        for ident in value['binding_ids']:
            row=_get(c,'bindings',ident)
            if row['archived'] and ident not in (current or {}).get('binding_ids',[]):raise ValueError('Нельзя добавить архивное назначение в набор')
    return value

def _write(c,kind,row,create):
    values=[row['revision'],row['platform'],row['name'],int(row['archived']),dump(row)]
    if create:
        cols=['id','revision','platform','name','archived','snapshot'];values=[row['id'],*values]
        if kind=='bindings':
            cols+=['connection_id','identity_id','destination_id'];values += [row[k] for k in cols[-3:]]
        c.execute(f'INSERT INTO {table(kind)}({",".join(cols)}) VALUES ({",".join("?" for _ in cols)})',values)
    else:c.execute(f'UPDATE {table(kind)} SET revision=?,platform=?,name=?,archived=?,snapshot=? WHERE id=?',(*values,row['id']))
    if kind=='sets':
        c.execute('DELETE FROM social_set_members WHERE set_id=?',(row['id'],))
        c.executemany('INSERT INTO social_set_members VALUES (?,?)',[(row['id'],b) for b in row['binding_ids']])
    c.execute('INSERT INTO social_revisions VALUES (?,?,?,?)',(kind,row['id'],row['revision'],dump(row)))

def save(store,args):
    kind=args.get('kind');table(kind);ident=args.get('id');data=args.get('data')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');current=_get(c,kind,ident) if ident else None
        if current:
            expected=integer(args.get('expected_revision'),'Версия',1)
            if current['revision']!=expected:raise ValueError('revision_conflict: запись изменена; загрузите сохранённую версию')
        if not isinstance(data,dict):raise ValueError('Укажите данные записи')
        old=None
        if not current:
            key=text(args.get('request_key'),'Ключ запроса',128,True)
            old=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
            if old and old['command']!='social_create:'+kind:raise ValueError('request_conflict: ключ использован другой командой')
        merged={**({k:current[k] for k in FIELDS[kind]} if current else {}),**data}
        value=_validate(c,kind,merged,current or (json.loads(old['result']) if old else None))
        archived=args.get('archived',current['archived'] if current else False)
        if type(archived) is not bool:raise ValueError('Архив: нужен переключатель')
        if current:
            if all(current[k]==v for k,v in value.items()) and current['archived']==archived:return current
            row={**current,**value,'archived':archived,'revision':current['revision']+1,'updated_at':now()}
        else:
            digest=hashlib.sha256(dump({'kind':kind,'data':value,'archived':archived}).encode()).hexdigest()
            if old:
                if old['payload_hash']!=digest:raise ValueError('request_conflict: ключ использован с другими данными')
                return json.loads(old['result'])
            row={**value,'id':uuid.uuid4().hex,'revision':1,'archived':archived,'created_at':now(),'updated_at':now()}
        _write(c,kind,row,not current)
        if not current:c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'social_create:'+kind,digest,dump(row)))
        return row

def listing(store,args):
    kind=args.get('kind');table(kind);offset=integer(args.get('offset',0),'Смещение');limit=integer(args.get('limit',50),'Размер страницы',1,100)
    query=text(args.get('query',''),'Поиск',240).casefold();archived=args.get('archived',False);platform=args.get('platform','')
    if type(archived) is not bool or platform not in ('',*PLATFORMS):raise ValueError('Неверный фильтр')
    where='archived=? AND instr(casefold(name),?)>0';params=[int(archived),query]
    if platform:where+=' AND platform=?';params.append(platform)
    with store.db() as c:
        c.create_function('casefold',1,str.casefold,deterministic=True)
        total=c.execute(f'SELECT count(*) FROM {table(kind)} WHERE {where}',params).fetchone()[0]
        rows=c.execute(f'SELECT id,revision,platform,name,archived FROM {table(kind)} WHERE {where} ORDER BY name,id LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
        return {'items':[{**dict(r),'archived':bool(r['archived'])} for r in rows],'total':total}

def binding_snapshot(c,ident):
    binding=_get(c,'bindings',ident)
    related={k:_get(c,kind,binding[k+'_id']) for k,kind in [('connection','connections'),('identity','identities'),('destination','destinations')]}
    if binding['archived'] or any(r['archived'] for r in related.values()):raise ValueError('Назначение или связанный аккаунт в архиве')
    return {'binding':binding,**related}

COMMANDS={'content_social_list','content_social_get','content_social_save'}
def dispatch(store,cmd,args):
    if not isinstance(args,dict):raise ValueError('Некорректные параметры соцсетей')
    if cmd=='content_social_list':return listing(store,args)
    if cmd=='content_social_get':return get(store,args.get('kind'),args.get('id'))
    if cmd=='content_social_save':return save(store,args)
    raise ValueError('Неизвестная команда соцсетей')
