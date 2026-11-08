"""Shared article/news/video campaigns with immutable target selection."""
import hashlib
import json
import uuid
from . import social_store as social
from .store import dump,integer,now

def migrate(c):
    c.execute('CREATE TABLE content_campaigns(id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id), source_revision INTEGER NOT NULL, revision INTEGER NOT NULL, archived INTEGER NOT NULL,snapshot TEXT NOT NULL)')
    c.execute('CREATE TABLE content_variants(id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL REFERENCES content_campaigns(id),revision INTEGER NOT NULL,snapshot TEXT NOT NULL)')
    c.execute('CREATE TABLE content_variant_revisions(id TEXT NOT NULL REFERENCES content_variants(id),revision INTEGER NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,revision))')
    c.execute('CREATE TABLE content_packages(hash TEXT PRIMARY KEY,variant_id TEXT NOT NULL REFERENCES content_variants(id),variant_revision INTEGER NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL)')
    c.execute('CREATE TABLE content_manual_receipts(id TEXT PRIMARY KEY,package_hash TEXT NOT NULL REFERENCES content_packages(hash),state TEXT NOT NULL,permalink TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL)')

def ids(value,label):
    if not isinstance(value,list) or len(value)>100 or any(not isinstance(v,str) or not v or len(v)>128 for v in value):raise ValueError(label+': неверный список')
    return sorted(set(value))

def _campaign(c,ident):
    row=c.execute('SELECT snapshot FROM content_campaigns WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('Задание распространения не найдено')
    return json.loads(row['snapshot'])

def _variant(c,ident):
    row=c.execute('SELECT snapshot FROM content_variants WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('Вариант публикации не найден')
    return json.loads(row['snapshot'])

def _target(c,ident):
    snapshot=social.binding_snapshot(c,ident)
    # Explicit allowlist: no credential ref/token/profile config enters a package.
    return {key:{k:record[k] for k in fields} for key,fields in {
        'binding':('id','revision','route','role'),
        'connection':('id','platform','transport'),
        'identity':('id','revision','platform','name','kind','external_id','url'),
        'destination':('id','revision','platform','name','kind','external_id','url','language','audience','rules')}.items() for record in [snapshot[key]]}

def get(store,ident):
    with store.db() as c:
        campaign=_campaign(c,ident)
        variants=[json.loads(r['snapshot']) for r in c.execute('SELECT snapshot FROM content_variants WHERE campaign_id=? ORDER BY rowid',(ident,))]
        for variant in variants:
            receipts=c.execute("SELECT r.id,CASE WHEN x.receipt_id IS NULL THEN r.state ELSE 'withdrawn' END state,r.permalink,r.note,r.created_at,p.variant_revision,r.package_hash,x.reason withdrawn_reason FROM content_manual_receipts r JOIN content_packages p ON p.hash=r.package_hash LEFT JOIN content_manual_retractions x ON x.receipt_id=r.id WHERE p.variant_id=? ORDER BY r.rowid DESC LIMIT 10",(variant['id'],)).fetchall()
            variant['receipts']=[dict(r) for r in receipts]
        return {**campaign,'variants':variants}

def listing(store,mid,source_kind='article'):
    if source_kind not in ('article','video'):raise ValueError('Неизвестный тип источника')
    if source_kind=='article':store.get(mid)
    else:
        from . import video_store
        video_store.get(store,mid)
    with store.db() as c:
        column='material_id' if source_kind=='article' else 'video_id'
        rows=c.execute('SELECT id,revision,archived,snapshot FROM content_campaigns WHERE '+column+'=? ORDER BY rowid DESC LIMIT 100',(mid,)).fetchall()
        return [{'id':r['id'],'revision':r['revision'],'archived':bool(r['archived']),'name':json.loads(r['snapshot'])['name']} for r in rows]

def create(store,args):
    if set(args)-{'id','source_revision','source_kind','render_import_id','review_revision','binding_ids','set_ids','request_key','name'}:raise ValueError('Некорректные поля задания')
    source_kind=args.get('source_kind','article')
    if source_kind not in ('article','video'):raise ValueError('Неизвестный тип источника')
    if source_kind=='article' and ('render_import_id' in args or 'review_revision' in args):raise ValueError('Параметры видео недопустимы для статьи')
    mid=social.text(args.get('id'),'Материал',128,True);revision=integer(args.get('source_revision'),'Версия источника',1)
    bindings=ids(args.get('binding_ids',[]),'Назначения');sets=ids(args.get('set_ids',[]),'Наборы')
    key=social.text(args.get('request_key'),'Ключ запроса',128,True);name=social.text(args.get('name',''),'Название задания',240)
    payload={'id':mid,'revision':revision,'bindings':bindings,'sets':sets,'name':name}
    if source_kind=='video':payload.update(source_kind='video',render_import_id=args.get('render_import_id'),review_revision=args.get('review_revision'))
    digest=hashlib.sha256(dump(payload).encode()).hexdigest()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if old:
            if old['command']!='campaign_create' or old['payload_hash']!=digest:raise ValueError('request_conflict: ключ уже использован')
            cid=json.loads(old['result'])['id']
        else:
            if source_kind=='video':
                from .video_campaigns import source_in
                source_ref,source=source_in(store,c,mid,revision,args.get('render_import_id'),args.get('review_revision'))
            else:
                source=store.revision(mid,revision)
                if store._get(c,mid)['archived']:raise ValueError('Исходный материал в архиве')
                source_ref={'kind':source['kind'],'id':mid,'revision':revision,'sha256':hashlib.sha256(dump(source).encode()).hexdigest(),'title':source['title']}
            selected_sets=[];chosen=set(bindings)
            for ident in sets:
                group=social._get(c,'sets',ident)
                if group['archived']:raise ValueError('Выбранный набор в архиве')
                selected_sets.append({'id':ident,'revision':group['revision'],'name':group['name']});chosen.update(group['binding_ids'])
            if not 1<=len(chosen)<=100:raise ValueError('Выберите от 1 до 100 назначений')
            targets={};duplicates=[]
            for ident in sorted(chosen):
                target=_target(c,ident);dest=target['destination']['id']
                if dest in targets:
                    prev=targets[dest]
                    if prev['identity']['id']!=target['identity']['id']:raise ValueError('Одно место выбрано от разных авторов. Создайте для второго автора отдельное задание.')
                    raise ValueError('Одно место выбрано через разные подключения. Выберите одно точное назначение.')
                targets[dest]=target
            cid=uuid.uuid4().hex
            campaign={'id':cid,'material_id':mid if source_kind=='article' else None,'source':source_ref,'name':name or ('Распространение: '+source['title'])[:240],'revision':1,'archived':False,'created_at':now(),'sets':selected_sets,'deduplicated_binding_ids':duplicates}
            if source_kind=='video':campaign['video_id']=mid
            c.execute('INSERT INTO content_campaigns(id,material_id,source_revision,revision,archived,snapshot,video_id) VALUES (?,?,?,?,?,?,?)',(cid,campaign['material_id'],revision,1,0,dump(campaign),mid if source_kind=='video' else None))
            initial=source['title']+'\n\n'+source['body' if source_kind=='article' else 'brief']
            if len(initial)>10000:initial=''
            for target in targets.values():
                variant={'id':uuid.uuid4().hex,'campaign_id':cid,'revision':1,'source':source_ref,'target':target,'text':initial,'canonical_url':'','created_at':now(),'updated_at':now(),'status':'draft'}
                _write(c,variant,True)
            c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'campaign_create',digest,dump({'id':cid})))
    return get(store,cid)

def _write(c,variant,create=False):
    if create:c.execute('INSERT INTO content_variants VALUES (?,?,?,?)',(variant['id'],variant['campaign_id'],variant['revision'],dump(variant)))
    else:c.execute('UPDATE content_variants SET revision=?,snapshot=? WHERE id=?',(variant['revision'],dump(variant),variant['id']))
    c.execute('INSERT INTO content_variant_revisions VALUES (?,?,?)',(variant['id'],variant['revision'],dump(variant)))

def update(store,args):
    ident=social.text(args.get('variant_id'),'Вариант',128,True);expected=integer(args.get('expected_revision'),'Версия варианта',1)
    value=social.text(args.get('text'),'Текст варианта',10000);url=args.get('canonical_url','')
    from .packages import public_url
    url=public_url(url) if url else ''
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');variant=_variant(c,ident);campaign=_campaign(c,variant['campaign_id'])
        if campaign['archived']:raise ValueError('Задание в архиве')
        if variant['revision']!=expected:raise ValueError('revision_conflict: вариант уже изменён')
        if variant['text']==value and variant['canonical_url']==url:return variant
        variant={**variant,'text':value,'canonical_url':url,'revision':expected+1,'updated_at':now(),'status':'draft'};_write(c,variant)
    return variant

def archive(store,args):
    expected=integer(args.get('expected_revision'),'Версия задания',1);value=args.get('archived')
    if type(value) is not bool:raise ValueError('Архив: нужен переключатель')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=_campaign(c,args.get('campaign_id'))
        if row['revision']!=expected:raise ValueError('revision_conflict: задание изменено')
        if row['archived']!=value:
            row={**row,'archived':value,'revision':expected+1}
            c.execute('UPDATE content_campaigns SET revision=?,archived=?,snapshot=? WHERE id=?',(row['revision'],int(value),dump(row),row['id']))
    return get(store,row['id'])

COMMANDS={'content_campaign_create','content_campaign_get','content_campaign_list','content_variant_save','content_campaign_archive','content_package_prepare','content_campaign_export','content_manual_report','content_manual_withdraw','content_manual_receipts','content_manual_receipt_get','content_video_campaign_choices','content_video_campaign_preview'}
def dispatch(store,cmd,args):
    if not isinstance(args,dict):raise ValueError('Некорректные параметры распространения')
    if cmd=='content_campaign_create':return create(store,args)
    if cmd=='content_campaign_get':return get(store,args.get('campaign_id'))
    if cmd=='content_campaign_list':return listing(store,args.get('id'),args.get('source_kind','article'))
    if cmd=='content_video_campaign_choices':
        from .video_campaigns import choices
        return choices(store,args.get('id'))
    if cmd=='content_video_campaign_preview':
        from .video_campaigns import preview
        return preview(store,args.get('campaign_id'))
    if cmd=='content_variant_save':return update(store,args)
    if cmd=='content_campaign_archive':return archive(store,args)
    from . import packages,manual
    if cmd=='content_package_prepare':return packages.prepare(store,args)
    if cmd=='content_campaign_export':return manual.export(store,args.get('campaign_id'))
    if cmd=='content_manual_report':return manual.report(store,args)
    if cmd=='content_manual_withdraw':return manual.withdraw(store,args)
    if cmd=='content_manual_receipts':return manual.page(store,args)
    if cmd=='content_manual_receipt_get':return manual.receipt_get(store,args)
    raise ValueError('Неизвестная команда распространения')
