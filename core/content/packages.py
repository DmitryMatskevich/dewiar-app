"""Stable content package identity, independent of credentials and expiring URLs."""
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit
from .store import dump,integer,now
from . import distribution as dist

def public_url(value):
    from .social_store import text
    value=text(value,'Публичная ссылка',2000,True)
    try:u=urlsplit(value);port=u.port
    except ValueError as e:raise ValueError('Некорректная публичная ссылка') from e
    host=(u.hostname or '').lower()
    if u.scheme!='https' or not host or u.username or u.password or port not in (None,443) or re.search(r'[\s\\\x00-\x1f]',value) or host=='localhost' or host.endswith(('.local','.localhost')):raise ValueError('Нужна публичная HTTPS-ссылка без логина и пароля')
    try:
        if not ipaddress.ip_address(host).is_global:raise ValueError('Локальный адрес не является публичным permalink')
    except ValueError as e:
        if 'permalink' in str(e):raise
    return value

def prepare_in(c,variant,store=None):
    campaign=dist._campaign(c,variant['campaign_id'])
    if campaign['archived']:raise ValueError('Задание в архиве')
    current=dist._target(c,variant['target']['binding']['id'])
    if current!=variant['target']:raise ValueError('stale_target: назначение изменилось; создайте новое задание из актуального каталога')
    if not variant['text'].strip():raise ValueError('Введите отдельный текст для каждого назначения; длинная статья автоматически не обрезается')
    payload={'schema_version':1,'source':variant['source'],'variant_id':variant['id'],'variant_revision':variant['revision'],'target':variant['target'],
             'text':variant['text']+ ('\n\n'+variant['canonical_url'] if variant['canonical_url'] else ''),'canonical_url':variant['canonical_url'],
             'format':'text','assets':[],'visibility':'destination_default','policy_version':'manual-text-v1'}
    if variant['source']['kind'] in ('video','reel'):
        if store is None:raise ValueError('API-очередь видео ещё не подключена; используйте ручной пакет')
        from .video_campaigns import validate_in
        validate_in(store,c,variant['source'])
        payload.update(schema_version=2,format='video',assets=[variant['source']['asset']],
                       captions=variant['source']['caption_ref'],policy_version='manual-video-v1')
    digest=hashlib.sha256(dump(payload).encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO content_packages VALUES (?,?,?,?,?)',(digest,variant['id'],variant['revision'],dump(payload),now()))
    return {'hash':digest,'payload':payload,'publication_status':'not_published','authorization_status':'not_authorized'}

def prepare(store,args):
    expected=integer(args.get('expected_revision'),'Версия варианта',1)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');variant=dist._variant(c,args.get('variant_id'))
        if variant['revision']!=expected:raise ValueError('revision_conflict: вариант изменился')
        return prepare_in(c,variant,store)
