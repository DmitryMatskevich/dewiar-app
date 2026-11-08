"""Accepted render references for the shared social campaign/package workflow."""
import hashlib
import json
from pathlib import PurePosixPath
from .store import dump,integer
from . import video_store,video_review,video_assets

MIMES={'.mp4':'video/mp4','.mov':'video/quicktime','.webm':'video/webm','.mkv':'video/x-matroska'}


def source_in(store,c,ident,revision,import_id,review_revision):
    video=video_store._get(c,ident)
    if video['revision']!=revision or video['archived']:
        raise ValueError('stale_video: нужна текущая версия активного видео')
    report=video_review.get_in(store,c,import_id)
    review=report['review']
    if not report['accepted'] or not review or review['revision']!=integer(review_revision,'Версия приёмки',1):
        raise ValueError('stale_video: выберите актуальное принятое видео после просмотра')
    if review['video_id']!=ident or review['video_revision']!=revision:
        raise ValueError('Файл относится к другому видео или сценарию')
    media,_,_=video_review._source(c,import_id)
    suffix=PurePosixPath(media['asset_path']).suffix
    video_assets.path_for(store,media)
    asset={'sha256':media['sha256'],'bytes':media['bytes'],'mime':MIMES[suffix],
           'duration_us':media['duration_us'],'filename':media['sha256']+suffix}
    ref={'kind':video['kind'],'id':ident,'revision':revision,'title':video['title'],
         'sha256':hashlib.sha256(dump(video).encode()).hexdigest(),'render_import_id':import_id,'asset':asset,
         'review':{'revision':review['revision'],'sha256':hashlib.sha256(dump(review).encode()).hexdigest()},
         'caption_ref':review['caption_ref']}
    return ref,video


def validate_in(store,c,ref):
    current,_=source_in(store,c,ref['id'],ref['revision'],ref['render_import_id'],ref['review']['revision'])
    if current!=ref:raise ValueError('stale_video: файл или редакционное решение изменились')


def choices(store,ident):
    with store.db() as c:
        video_store._get(c,ident)
        rows=c.execute("SELECT id FROM content_video_imports WHERE video_id=? AND state='imported' ORDER BY rowid DESC LIMIT 100",(ident,)).fetchall()
        result=[]
        for row in rows:
            report=video_review.get_in(store,c,row['id'])
            if report['accepted']:
                result.append({'import_id':row['id'],'review_revision':report['review']['revision'],
                    'video_revision':report['video_revision'],'asset_sha256':report['media']['sha256'],
                    'duration_us':report['media']['duration_us'],'caption_ref':report['review']['caption_ref']})
        return result


def caption_in(c,ref):
    caption=ref['caption_ref']
    if not caption:return None
    row=c.execute('SELECT snapshot FROM content_caption_revisions WHERE id=? AND revision=?',(caption['id'],caption['revision'])).fetchone()
    if not row:raise ValueError('Не найдена закреплённая версия субтитров')
    track=json.loads(row[0])
    if hashlib.sha256(dump(track).encode()).hexdigest()!=caption['sha256']:raise ValueError('Контрольная сумма субтитров не совпала')
    return track


def preview(store,campaign_id):
    from . import distribution,video_preview
    with store.db() as c:
        campaign=distribution._campaign(c,campaign_id)
        if campaign['archived']:raise ValueError('Задание в архиве')
        ref=campaign['source']
        if ref['kind'] not in ('video','reel'):raise ValueError('Нужно задание для видео')
        validate_in(store,c,ref);track=caption_in(c,ref)
    return {'url':video_preview.open_preview(store,ref['render_import_id'],track=track)}
