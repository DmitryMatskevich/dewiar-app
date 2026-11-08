"""Workspace-local site setup and release receipts. No production write capability."""
import json
from pathlib import Path
import uuid
import threading
import time
from ..store import dump, integer, now
from . import base, astro


def site(store,sid):
    s=next((s for s in store.sites() if s['id']==sid),None)
    if not s:raise ValueError('Неизвестный сайт')
    return s


def config(store,sid):
    s=site(store,sid)
    with store.db() as c:r=c.execute('SELECT * FROM site_connections WHERE site_id=?',(sid,)).fetchone()
    return {'site':s,'revision':r['revision'] if r else 0,'local_root':r['local_root'] if r else ''}


def configure(store,args):
    sid=args.get('site_id');s=site(store,sid);expected=integer(args.get('expected_revision'),'Версия подключения')
    root=args.get('local_root')
    if not isinstance(root,str) or not root or len(root)>4096:raise ValueError('Укажите локальный каталог Astro')
    captured=astro.capture(s,root)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT revision FROM site_connections WHERE site_id=?',(sid,)).fetchone()
        if (old['revision'] if old else 0)!=expected:raise ValueError('revision_conflict: настройка сайта изменена')
        c.execute('INSERT INTO site_connections VALUES (?,?,?) ON CONFLICT(site_id) DO UPDATE SET revision=excluded.revision,local_root=excluded.local_root',(sid,expected+1,captured['root']))
    return config(store,sid)


def inspect(store,sid):
    cfg=config(store,sid)
    if not cfg['local_root']:raise ValueError('Сначала укажите локальную папку проекта сайта')
    snap=astro.capture(cfg['site'],cfg['local_root'])
    return {**cfg,'categories':[{'id':str(c['id']),'title':str(c.get('title',''))} for c in snap['categories']],
            'baseline_sha256':snap['sha256'],'page_count':len(snap['pages']),'status':'local_source_verified','publication_status':'not_connected'}


def prepare(store,args):
    edition=base.get(store,args.get('edition_id'));expected=integer(args.get('expected_revision'),'Версия сайта',1)
    if edition['revision']!=expected:raise ValueError('revision_conflict: версия сайта изменилась')
    cfg=config(store,edition['site_id'])
    if not cfg['local_root']:raise ValueError('Локальный проект сайта не настроен')
    snap=astro.capture(cfg['site'],cfg['local_root']);plan=base.prepare(edition,cfg['site'],snap,astro.AstroAdapter())
    dry=args.get('dry_run',True)
    if type(dry) is not bool:raise ValueError('dry_run: нужен переключатель')
    if dry:return {'dry_run':True,'plan':plan}
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        current=base.get(store,edition['id'],c)
        if current['revision']!=expected:raise ValueError('revision_conflict: версия сайта изменилась во время подготовки')
        # Identical preparation returns the same local receipt, never another send.
        old=c.execute('SELECT id,plan FROM site_releases WHERE edition_id=? AND edition_revision=? ORDER BY rowid DESC',(edition['id'],expected)).fetchall()
        for r in old:
            if json.loads(r['plan'])['package_hash']==plan['package_hash']:return {'dry_run':False,'release_id':r['id'],'plan':plan}
        rid=uuid.uuid4().hex;c.execute('INSERT INTO site_releases VALUES (?,?,?,?,?,?,?)',(rid,edition['id'],expected,dump(plan),'prepared',None,now()))
    return {'dry_run':False,'release_id':rid,'plan':plan}


def releases(store,eid):
    base.get(store,eid)
    with store.db() as c:return [dict(r) for r in c.execute('SELECT id,edition_revision,state,receipt,created_at FROM site_releases WHERE edition_id=? ORDER BY rowid DESC LIMIT 30',(eid,))]


def release(store,rid):
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM site_releases WHERE id=?',(rid,)).fetchone()
        if not row:raise ValueError('Выпуск не найден')
        result=dict(row);result['plan']=json.loads(result['plan']);result['receipt']=json.loads(result['receipt']) if result['receipt'] else None
        if result['state']=='building' and (result['receipt'] or {}).get('deadline',0)<time.time():
            result['state']='failed';result['receipt']={'error':'Сборка прервана; автоматический повтор не выполнялся'}
            c.execute('UPDATE site_releases SET state=?,receipt=? WHERE id=?',('failed',dump(result['receipt']),rid))
        return result


def start_build(store,rid):
    item=release(store,rid)
    if item['state'] in ('building','preview_built'):return item
    edition=base.get(store,item['edition_id']);cfg=config(store,edition['site_id']);snap=astro.capture(cfg['site'],cfg['local_root'])
    base.validate_plan(item['plan'],edition,cfg['site'],snap)
    build_token=uuid.uuid4().hex
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        claimed=c.execute("UPDATE site_releases SET state='building',receipt=? WHERE id=? AND state IN ('prepared','failed')",(dump({'deadline':time.time()+210,'build_token':build_token}),rid))
        claimed=bool(claimed.rowcount)
    if not claimed:return release(store,rid)
    def run():
        from .build import build
        try:receipt=build(item['plan'],edition,cfg['site'],cfg['local_root'],store.root/'builds');state='preview_built'
        except Exception as e:receipt={'error':str(e)[:1000]};state='failed'
        finish_build(store,rid,build_token,state,receipt)
    thread=threading.Thread(target=run,daemon=True,name='content-site-build')
    try:thread.start()
    except Exception:
        finish_build(store,rid,build_token,'failed',{'error':'Не удалось запустить сборку'})
        raise
    return release(store,rid)


def finish_build(store,rid,build_token,state,receipt):
    # A superseded worker cannot finish a later retry of the same release.
    with store.db() as c:
        return bool(c.execute("UPDATE site_releases SET state=?,receipt=? WHERE id=? AND state='building' AND json_extract(receipt,'$.build_token')=?",(state,dump(receipt),rid,build_token)).rowcount)


COMMANDS={'content_edition_create','content_edition_get','content_edition_update','content_edition_list','content_site_config','content_site_configure','content_site_inspect','content_site_prepare','content_site_releases','content_site_release','content_site_build','content_site_preview'}


def dispatch(store,cmd,args):
    if cmd=='content_edition_create':return base.create(store,args)
    if cmd=='content_edition_get':return base.get(store,args.get('edition_id'))
    if cmd=='content_edition_update':return base.update(store,args)
    if cmd=='content_edition_list':return base.list_editions(store,args.get('id'))
    if cmd=='content_site_config':return config(store,args.get('site_id'))
    if cmd=='content_site_configure':return configure(store,args)
    if cmd=='content_site_inspect':return inspect(store,args.get('site_id'))
    if cmd=='content_site_prepare':return prepare(store,args)
    if cmd=='content_site_releases':return releases(store,args.get('edition_id'))
    if cmd=='content_site_release':return release(store,args.get('release_id'))
    if cmd=='content_site_build':return start_build(store,args.get('release_id'))
    if cmd=='content_site_preview':
        from .preview import open_preview
        item=release(store,args.get('release_id'))
        if item['state']!='preview_built':raise ValueError('Сначала соберите локальный preview')
        return {'url':open_preview(item['receipt'])}
    raise ValueError('Неизвестная команда публикации сайта')
