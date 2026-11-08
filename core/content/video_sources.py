"""Immutable article provenance pinned to video revisions, without inferred times."""
import json
from .store import integer


def migrate(c):
    c.execute('''CREATE TABLE content_video_revision_sources(
        video_id TEXT NOT NULL,revision INTEGER NOT NULL,position INTEGER NOT NULL,
        source_id TEXT NOT NULL REFERENCES content_source_records(id),
        PRIMARY KEY(video_id,revision,position),UNIQUE(video_id,revision,source_id),
        FOREIGN KEY(video_id,revision) REFERENCES content_video_revisions(id,revision))''')


def inherit(c,ref,snapshot):
    if ref['kind']=='article':
        from .source_records import records_in as article_records
        records=article_records(c,ref['id'],ref['revision'])
        return {'source_records_status':'captured','source_record_ids':[r['id'] for r in records]}
    records=records_in(c,snapshot)
    return {'source_records_status':snapshot.get('source_records_status','legacy_unavailable'),
            'source_record_ids':[r['id'] for r in records]}


def bind_in(c,row):
    ids=row.get('source_record_ids',[])
    if not isinstance(ids,list) or len(ids)>20 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('Некорректный состав источников видео')
    c.executemany('INSERT INTO content_video_revision_sources VALUES (?,?,?,?)',[(row['id'],row['revision'],i,ident) for i,ident in enumerate(ids)])


def records_in(c,row):
    rows=c.execute('SELECT s.id,s.snapshot FROM content_video_revision_sources v JOIN content_source_records s ON s.id=v.source_id WHERE v.video_id=? AND v.revision=? ORDER BY v.position',(row['id'],row['revision'])).fetchall()
    if [r['id'] for r in rows]!=row.get('source_record_ids',[]):raise ValueError('Состав источников видео не соответствует сохранённой версии')
    return [{'id':r['id'],'snapshot':json.loads(r['snapshot'])} for r in rows]


def dispatch(store,cmd,args):
    from .video_store import _get
    with store.db() as c:
        row=_get(c,args.get('id'),args.get('revision'));records=records_in(c,row)
        if cmd=='content_video_source_records':
            return {'status':row.get('source_records_status','legacy_unavailable' if row['source_ref'] else 'none'),
                    'video_revision':row['revision'],
                    'items':[{'source_id':r['id'],**{k:v for k,v in r['snapshot'].items() if k not in ('text','segments_v1')},'segment_count':len(r['snapshot'].get('segments_v1',{}).get('items',[])),'text_bytes':len(r['snapshot'].get('text','').encode())} for r in records]}
        found=next((r for r in records if r['id']==args.get('source_id')),None)
        if found is None:raise ValueError('Источник не входит в выбранную версию видео')
        source=found['snapshot'];text=source.get('text','')
        if cmd=='content_video_source_segments':
            value=source.get('segments_v1')
            if value:
                from .source_segments import validate
                validate(text,value)
            return {'source_id':found['id'],'items':value['items'] if value else [],'timing_origin':value['timing_origin'] if value else None,'alignment_verified':False,'media_ref':None}
        if source.get('asset_id'):
            from .assets import read
            text=read(store,source['asset_id']).decode('utf-8')
        offset=integer(args.get('offset',0),'Смещение',0,1048576);limit=integer(args.get('limit',20000),'Размер фрагмента',1,20000)
        return {'text':text[offset:offset+limit],'offset':offset,'total':len(text),'source_id':found['id']}
