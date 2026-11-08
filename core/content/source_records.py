"""Immutable attributed sources, ordered independently for each article revision."""
import hashlib
import json
import re
from .store import dump,integer

MAX_BYTES=1048576
FIELDS={'source_kind','task_id','path','task_title','source_updated','sha256','text','timing','quality','asset_id','provenance','origin_url','attribution','segments_v1'}
COMMANDS={'content_source_change','content_source_records','content_source_record_text','content_source_segments_get'}


def migrate(c):
    c.execute('CREATE TABLE content_source_records(id TEXT PRIMARY KEY,snapshot TEXT NOT NULL)')
    c.execute('''CREATE TABLE content_revision_sources(material_id TEXT NOT NULL,revision INTEGER NOT NULL,position INTEGER NOT NULL,
        source_id TEXT NOT NULL REFERENCES content_source_records(id),PRIMARY KEY(material_id,revision,position),
        UNIQUE(material_id,revision,source_id),FOREIGN KEY(material_id,revision) REFERENCES revisions(material_id,revision))''')
    for legacy in c.execute('SELECT material_id,snapshot FROM sources').fetchall():
        # Preserve the original serialized snapshot and every old revision byte.
        ident='src-'+hashlib.sha256(legacy['snapshot'].encode()).hexdigest()
        c.execute('INSERT OR IGNORE INTO content_source_records VALUES (?,?)',(ident,legacy['snapshot']))
        c.execute('INSERT INTO content_revision_sources SELECT material_id,revision,0,? FROM revisions WHERE material_id=?',(ident,legacy['material_id']))


def validate_many(sources):
    if not isinstance(sources,list) or len(sources)>20:raise ValueError('У статьи может быть до 20 источников')
    for row in sources:
        if not isinstance(row,dict) or set(row)-FIELDS:raise ValueError('Некорректные поля источника')
        if not isinstance(row.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',row['sha256']):raise ValueError('Некорректная контрольная сумма источника')
        for key,value in row.items():
            if key=='segments_v1':
                from .source_segments import validate
                validate(row.get('text',''),value)
                if row.get('asset_id'):raise ValueError('HTML-оригинал не поддерживает разметку текстовых фрагментов')
                continue
            if key=='source_updated' and value is None:continue
            if not isinstance(value,str) or '\x00' in value:raise ValueError('Источник содержит неверный текст')
            if key!='text' and len(value)>2000:raise ValueError('Слишком длинные сведения об источнике')
        if len(row.get('text','').encode())>MAX_BYTES:raise ValueError('Источник больше 1 МиБ')
        if row.get('asset_id') and not re.fullmatch('[0-9a-f]{64}',row['asset_id']):raise ValueError('Некорректный оригинал источника')
    if len({dump(s) for s in sources})!=len(sources):raise ValueError('Одинаковый источник уже включён')
    return sources


def put_in(c,row):
    validate_many([row]);encoded=dump(row);ident='src-'+hashlib.sha256(encoded.encode()).hexdigest()
    if row.get('asset_id') and not c.execute('SELECT 1 FROM content_assets WHERE sha256=?',(row['asset_id'],)).fetchone():raise ValueError('Оригинал источника не зарегистрирован')
    c.execute('INSERT OR IGNORE INTO content_source_records VALUES (?,?)',(ident,encoded));return ident


def ids_in(c,mid,revision):
    return [r[0] for r in c.execute('SELECT source_id FROM content_revision_sources WHERE material_id=? AND revision=? ORDER BY position',(mid,revision))]


def bind_in(c,mid,revision,ids):
    if len(ids)>20 or len(set(ids))!=len(ids):raise ValueError('Повторные источники или превышен лимит 20')
    c.executemany('INSERT INTO content_revision_sources VALUES (?,?,?,?)',[(mid,revision,i,ident) for i,ident in enumerate(ids)])


def records_in(c,mid,revision):
    integer(revision,'Версия материала',1)
    if not c.execute('SELECT 1 FROM revisions WHERE material_id=? AND revision=?',(mid,revision)).fetchone():raise ValueError('Версия материала не найдена')
    return [{'id':r['id'],'snapshot':json.loads(r['snapshot'])} for r in c.execute('SELECT s.id,s.snapshot FROM content_revision_sources r JOIN content_source_records s ON s.id=r.source_id WHERE r.material_id=? AND r.revision=? ORDER BY r.position',(mid,revision))]


def listing(store,args):
    with store.db() as c:
        row=store._get(c,args.get('id'));revision=integer(args.get('revision',row['revision']),'Версия материала',1)
        from .source_segments import summary
        return [{'source_id':r['id'],**{k:v for k,v in r['snapshot'].items() if k not in ('text','segments_v1')},'text_bytes':len(r['snapshot'].get('text','').encode()),**summary(r['snapshot'])} for r in records_in(c,row['id'],revision)]


def change(store,args,tasks):
    if set(args)-{'id','expected_revision','operation','source_id','source_ids','task_id','path','note','request_key','segments'}:raise ValueError('Некорректные параметры источников')
    expected=integer(args.get('expected_revision'),'Версия материала',1);op=args.get('operation');key=args.get('request_key')
    if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('Нужен ключ запроса источников')
    if op not in ('add','replace','remove','reorder','segments'):raise ValueError('Неизвестное действие с источником')
    # Digest request intent first: a retry must return the committed result even
    # when an external transcript has since changed or disappeared.
    digest=hashlib.sha256(dump({k:v for k,v in args.items() if k!='request_key'}).encode()).hexdigest()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if old:
            if old['command']!='source_change' or old['payload_hash']!=digest:raise ValueError('request_conflict: запрос источников изменился')
            return json.loads(old['result'])
        row=store._get(c,args.get('id'))
        if row['revision']!=expected:raise ValueError('revision_conflict: состав или текст материала уже изменён')
        if row['archived']:raise ValueError('Материал в архиве')
        chosen=ids_in(c,row['id'],expected)
        if op=='segments':
            from .source_segments import annotation
            if args.get('source_id') not in chosen:raise ValueError('Источник не входит в текущую версию')
            source=json.loads(c.execute('SELECT snapshot FROM content_source_records WHERE id=?',(args['source_id'],)).fetchone()[0])
            if source.get('asset_id'):raise ValueError('Разметка доступна для текстовых расшифровок и заметок')
            normalized=annotation(source.get('text',''),args.get('segments'))
            if normalized['items']:source['segments_v1']=normalized
            else:source.pop('segments_v1',None)
            chosen[chosen.index(args['source_id'])]=put_in(c,source)
        elif op in ('add','replace'):
            if 'note' in args:
                note=args['note']
                if not isinstance(note,dict) or set(note)-{'title','text','url'}:raise ValueError('Некорректная заметка источника')
                title=note.get('title','');text=note.get('text','');url=note.get('url','')
                if not isinstance(title,str) or not 1<=len(title.strip())<=240 or not isinstance(text,str) or not text.strip():raise ValueError('Укажите название и текст источника')
                if url:
                    from .packages import public_url
                    url=public_url(url)
                source={'source_kind':'note','task_title':title.strip(),'text':text,'origin_url':url,'sha256':hashlib.sha256(text.encode()).hexdigest(),'quality':'requires_editor_review','provenance':'editor_supplied','timing':'source_text_only'}
            else:
                from .sources import snapshot
                source=snapshot(tasks,args.get('task_id'),args.get('path'))
            ident=put_in(c,source)
            if ident in chosen:raise ValueError('Такой источник уже включён в материал')
            if op=='add':chosen.append(ident)
            else:
                if args.get('source_id') not in chosen:raise ValueError('Заменяемый источник не входит в эту версию')
                chosen[chosen.index(args['source_id'])]=ident
        elif op=='remove':
            if args.get('source_id') not in chosen:raise ValueError('Источник не входит в эту версию')
            chosen.remove(args['source_id'])
        else:
            ids=args.get('source_ids')
            if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids) or len(ids)!=len(chosen) or set(ids)!=set(chosen):raise ValueError('Порядок должен содержать все текущие источники ровно один раз')
            chosen=ids
        result=store.update(row['id'],expected,{},_c=c,_source_ids=chosen)
        c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'source_change',digest,dump(result)));return result


def dispatch(store,cmd,args,tasks=None):
    if not isinstance(args,dict):raise ValueError('Некорректная команда источников')
    if cmd=='content_source_change':return change(store,args,tasks or {})
    if cmd=='content_source_records':return listing(store,args)
    if cmd=='content_source_segments_get':
        from .source_segments import get
        return get(store,args)
    with store.db() as c:
        row=store._get(c,args.get('id'));records=records_in(c,row['id'],args.get('revision',row['revision']))
        found=next((r for r in records if r['id']==args.get('source_id')),None)
        if not found:raise ValueError('Источник не входит в эту версию')
        source=found['snapshot'];text=source.get('text','')
        if source.get('asset_id'):
            from .assets import read
            text=read(store,source['asset_id']).decode('utf-8')
        offset=integer(args.get('offset',0),'Смещение',0,MAX_BYTES);limit=integer(args.get('limit',20000),'Размер фрагмента',1,20000)
        return {'text':text[offset:offset+limit],'total':len(text),'offset':offset,'source_id':found['id']}
