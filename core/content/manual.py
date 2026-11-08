"""Manual export and unverified human reports, never a remote publishing success."""
import base64
import hashlib
import io
import json
import uuid
import zipfile
from urllib.parse import urlsplit,parse_qsl
from . import distribution as dist,packages,social_store
from .store import dump,now,integer

RECEIPT_SELECT="""SELECT r.id,CASE WHEN x.receipt_id IS NULL THEN r.state ELSE 'withdrawn' END state,
    r.permalink,r.note,r.created_at,p.variant_revision,r.package_hash,x.reason withdrawn_reason
    FROM content_manual_receipts r JOIN content_packages p ON p.hash=r.package_hash
    LEFT JOIN content_manual_retractions x ON x.receipt_id=r.id"""


def page(store,args):
    """Append-only receipt order; resolve cursor IDs afresh, never trust a raw rowid."""
    if set(args)-{'variant_id','cursor','limit'}:raise ValueError('Некорректные поля журнала')
    ident=social_store.text(args.get('variant_id'),'Вариант',128,True)
    limit=integer(args.get('limit',10),'Размер страницы',1,50);cursor=args.get('cursor')
    with store.db() as c:
        dist._variant(c,ident)
        def position(receipt_id):
            social_store.text(receipt_id,'Курсор отчёта',128,True)
            row=c.execute('SELECT r.rowid AS position FROM content_manual_receipts r JOIN content_packages p ON p.hash=r.package_hash WHERE r.id=? AND p.variant_id=?',(receipt_id,ident)).fetchone()
            if row is None:raise ValueError('Курсор не относится к журналу этого поста. Обновите журнал.')
            return row['position']
        if cursor is None:
            top=c.execute('SELECT r.id,r.rowid AS position FROM content_manual_receipts r JOIN content_packages p ON p.hash=r.package_hash WHERE p.variant_id=? ORDER BY r.rowid DESC LIMIT 1',(ident,)).fetchone()
            if top is None:return {'items':[],'total':0,'cursor':None,'next_cursor':None}
            cursor={'anchor':top['id'],'before':None};upper=top['position'];before=None
        else:
            if not isinstance(cursor,dict) or set(cursor)!={'anchor','before'}:raise ValueError('Некорректный курсор журнала')
            upper=position(cursor['anchor']);before=position(cursor['before']) if cursor['before'] is not None else None
            if before is not None and before>upper:raise ValueError('Курсор выходит за границы выбранного просмотра')
        params=[ident,upper];where=' WHERE p.variant_id=? AND r.rowid<=?'
        total=c.execute('SELECT count(*) FROM content_manual_receipts r JOIN content_packages p ON p.hash=r.package_hash'+where,params).fetchone()[0]
        if before is not None:where+=' AND r.rowid<?';params.append(before)
        rows=c.execute(RECEIPT_SELECT+where+' ORDER BY r.rowid DESC LIMIT ?',[*params,limit+1]).fetchall()
        items=[dict(r) for r in rows[:limit]]
        return {'items':items,'total':total,'cursor':cursor,'next_cursor':{'anchor':cursor['anchor'],'before':items[-1]['id']} if len(rows)>limit else None}


def receipt_get(store,args):
    if set(args)!={'receipt_id'}:raise ValueError('Укажите отчёт для просмотра')
    ident=social_store.text(args.get('receipt_id'),'Отчёт',128,True)
    with store.db() as c:
        row=c.execute(RECEIPT_SELECT+' WHERE r.id=?',(ident,)).fetchone()
        if row is None:raise ValueError('Ручной отчёт не найден')
        raw=c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(row['package_hash'],)).fetchone()[0]
        payload=json.loads(raw)
        if hashlib.sha256(dump(payload).encode()).hexdigest()!=row['package_hash']:raise ValueError('Зафиксированный пакет повреждён')
        return {'receipt':dict(row),'payload':payload}

def migrate(c):
    c.execute('CREATE TABLE content_manual_retractions(receipt_id TEXT PRIMARY KEY REFERENCES content_manual_receipts(id),reason TEXT NOT NULL,created_at TEXT NOT NULL)')

def permalink(platform,value):
    value=packages.public_url(value);url=urlsplit(value)
    if not url.path.strip('/') or not any(url.hostname==host or url.hostname.endswith('.'+host) for host in social_store.HOSTS[platform]):
        raise ValueError('Ссылка должна вести на пост в выбранной соцсети')
    # Post URLs can legitimately carry IDs in their query. Profile URL rules
    # are stricter and must not be reused for a user-reported permalink.
    if any(key.casefold() in {'access_token','refresh_token','token','code','client_secret','password'} for key,_ in parse_qsl(url.query)):
        raise ValueError('Ссылка на пост не должна содержать параметры входа')
    return value

def export(store,ident):
    files={};manifest={'schema_version':1,'campaign_id':ident,'publication_status':'not_published','packages':[]}
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');dist._campaign(c,ident)
        rows=c.execute('SELECT snapshot FROM content_variants WHERE campaign_id=? ORDER BY rowid',(ident,)).fetchall()
        for row in rows:
            variant=json.loads(row['snapshot']);package=packages.prepare_in(c,variant,store);prefix='destinations/'+variant['id']+'/'
            files[prefix+'post.txt']=package['payload']['text'].encode();files[prefix+'target.json']=dump(package['payload']['target']).encode()
            files[prefix+'package.json']=dump(package['payload']).encode();manifest['packages'].append({'variant_id':variant['id'],'revision':variant['revision'],'hash':package['hash']})
            if package['payload']['format']=='video':
                ref=variant['source'];manifest.update(schema_version=2,media_delivery='separate_authenticated_download')
                files['media/video.json']=dump(ref['asset']).encode()
                from .video_campaigns import caption_in
                from .captions import render
                track=caption_in(c,ref)
                if track:
                    for fmt in ('srt','vtt'):files['media/captions.'+fmt]=render(track,fmt).encode()
    files['README.txt']='Ручной пакет Dewiar. Ничего не опубликовано.\nПеред отправкой проверьте точный аккаунт, группу, текст и правила площадки.\nОтчёт пользователя о публикации хранится отдельно от проверки сервисом.\n'.encode()
    if manifest.get('media_delivery'):
        files['README.txt']+='\nВидеофайл скачивается отдельно кнопкой «Просмотр и скачивание видео» в задании Dewiar. В ZIP его нет: это позволяет передавать файлы больше лимита моста. Сверьте имя, размер и SHA256 с media/video.json.\nДорожка SRT/VTT приложена отдельно, она не вшита в кадры. Если площадка не принимает отдельную дорожку, подготовьте render с титрами и снова выполните приёмку.\n'.encode()
    manifest['files']={name:hashlib.sha256(data).hexdigest() for name,data in files.items()};files['manifest.json']=dump(manifest).encode()
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():z.writestr(name,data)
    if len(buf.getvalue())>10*1024*1024:raise ValueError('Пакет больше лимита моста; уменьшите число назначений')
    return {'filename':'distribution-'+ident+'.zip','mime':'application/zip','b64':base64.b64encode(buf.getvalue()).decode(),'manifest':manifest}

def report(store,args):
    if set(args)-{'package_hash','permalink','note'}:raise ValueError('Некорректные поля ручного отчёта')
    package_hash=social_store.text(args.get('package_hash'),'Hash пакета',64,True)
    url=packages.public_url(args.get('permalink'));note=social_store.text(args.get('note',''),'Примечание',2000)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT snapshot FROM content_packages WHERE hash=?',(package_hash,)).fetchone()
        if not row:raise ValueError('Подготовленный пакет не найден')
        package=json.loads(row['snapshot']);platform=package['target']['destination']['platform']
        url=permalink(platform,url)
        if package['target']['binding']['route']!='manual':raise ValueError('Для ручного отчёта нужно назначение с ручным маршрутом')
        previous=c.execute('SELECT r.id,r.state,r.permalink,r.note,r.created_at FROM content_manual_receipts r LEFT JOIN content_manual_retractions x ON x.receipt_id=r.id WHERE r.package_hash=? AND r.permalink=? AND x.receipt_id IS NULL',(package_hash,url)).fetchone()
        if previous:
            if previous['note']!=note:raise ValueError('Для этой ссылки уже есть отчёт с другим примечанием. Отзовите ошибочную запись перед исправлением.')
            return dict(previous)
        result={'id':uuid.uuid4().hex,'state':'reported_published','permalink':url,'note':note,'created_at':now()}
        c.execute('INSERT INTO content_manual_receipts VALUES (?,?,?,?,?,?)',(result['id'],package_hash,result['state'],url,note,result['created_at']))
        return result

def withdraw(store,args):
    if set(args)-{'receipt_id','reason'}:raise ValueError('Некорректные поля отзыва отчёта')
    ident=social_store.text(args.get('receipt_id'),'Отчёт',128,True)
    reason=social_store.text(args.get('reason'),'Причина отзыва',2000,True)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM content_manual_receipts WHERE id=?',(ident,)).fetchone():raise ValueError('Ручной отчёт не найден')
        old=c.execute('SELECT reason,created_at FROM content_manual_retractions WHERE receipt_id=?',(ident,)).fetchone()
        if old:
            if old['reason']!=reason:raise ValueError('Отчёт уже отозван с другой причиной')
            return {'receipt_id':ident,'state':'withdrawn',**dict(old)}
        result={'receipt_id':ident,'state':'withdrawn','reason':reason,'created_at':now()}
        c.execute('INSERT INTO content_manual_retractions VALUES (?,?,?)',(ident,reason,result['created_at']))
        return result
