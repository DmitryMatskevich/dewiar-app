"""Versioned site editions and pure release preparation; no network publication."""
from datetime import datetime
import hashlib
import json
import re
import uuid
from ..store import dump, now, integer
from ..models import validate_blocks, body_from_blocks

EDITION_FIELDS={'title','description','slug','language','publication_date','blocks','author','category_id','author_credit'}


def validate(data):
    if not isinstance(data,dict) or set(data)-EDITION_FIELDS:raise ValueError('Некорректные поля версии для сайта')
    out={'title':'','description':'','slug':'','language':'ru','publication_date':'','blocks':[], 'author':'','category_id':'',**data}
    for key,limit in [('title',240),('description',1000),('slug',120),('author',240),('category_id',100),('publication_date',40)]:
        if not isinstance(out[key],str) or len(out[key])>limit or '\x00' in out[key]:raise ValueError('Некорректное поле версии: '+key)
    if not out['title'].strip():raise ValueError('Укажите заголовок для сайта')
    if out['language'] not in ('ru','en'):raise ValueError('Язык версии: ru или en')
    if out['slug'] and not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',out['slug']):raise ValueError('Адрес статьи: латинские буквы, цифры и дефисы')
    if out['publication_date']:
        try:date=datetime.fromisoformat(out['publication_date'])
        except ValueError as e:raise ValueError('Некорректная дата публикации') from e
        if date.tzinfo is None:raise ValueError('Дата публикации должна содержать часовой пояс')
    from ..author_credit import validate as validate_credit
    credit=validate_credit(out.pop('author_credit',None),out['author'])
    if credit is not None:out['author_credit']=credit
    out['blocks']=validate_blocks(out['blocks']);return out


def get(store,eid,c=None):
    if c is None:
        with store.db() as db:return get(store,eid,db)
    row=c.execute('SELECT snapshot FROM site_editions WHERE id=?',(eid,)).fetchone()
    if not row:raise ValueError('Версия сайта не найдена')
    return json.loads(row['snapshot'])


def list_editions(store,mid):
    store.get(mid)
    with store.db() as c:
        return [dict(r) for r in c.execute('SELECT id,revision,site_id,language,title,archived FROM site_editions WHERE material_id=? ORDER BY rowid DESC',(mid,))]


def record(c,row):
    c.execute('INSERT INTO site_editions VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,language=excluded.language,title=excluded.title,archived=excluded.archived,snapshot=excluded.snapshot',
              (row['id'],row['material_id'],row['revision'],row['site_id'],row['language'],row['title'],int(row['archived']),dump(row)))
    c.execute('INSERT INTO site_edition_revisions VALUES (?,?,?)',(row['id'],row['revision'],dump(row)))


def create(store,args):
    material=store.revision(args.get('id'),args.get('source_revision'));site_id=args.get('site_id');key=args.get('request_key')
    if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('Нужен ключ создания версии сайта')
    from ..author_credit import for_site,carry,validate as validate_credit
    initial={'title':material['title'],'author':material['author'],'blocks':material['blocks']}
    credit=for_site(material.get('author_credit'),site_id)
    if credit is not None:initial.update(author=credit['byline'],author_credit=credit)
    data=validate(carry(initial,args.get('data',{})))
    digest=hashlib.sha256(dump({'material_id':material['id'],'source_revision':material['revision'],'site_id':site_id,'data':data}).encode()).hexdigest()
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM site_profiles WHERE id=?',(site_id,)).fetchone():raise ValueError('Неизвестный сайт')
        old=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if old:
            if old['command']!='edition_create' or old['payload_hash']!=digest:raise ValueError('request_conflict: ключ версии уже использован')
            return json.loads(old['result'])
        validate_credit(data.get('author_credit'),data['author'],c=c)
        if data.get('author_credit') and data['author_credit']['site_id']!=site_id:raise ValueError('Сведения об авторе выбраны для другого сайта')
        row={**data,'id':uuid.uuid4().hex,'revision':1,'material_id':material['id'],'source_revision':material['revision'],'site_id':site_id,'archived':False,'created_at':now(),'updated_at':now()}
        record(c,row);c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'edition_create',digest,dump(row)));return row


def update(store,args):
    expected=integer(args.get('expected_revision'),'Версия сайта',1);patch=args.get('patch',{})
    if not isinstance(patch,dict) or set(patch)-EDITION_FIELDS:raise ValueError('Некорректные поля версии сайта')
    archived=args.get('archived')
    if archived is not None and type(archived) is not bool:raise ValueError('Архив: нужен переключатель')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=get(store,args.get('edition_id'),c)
        if row['revision']!=expected:raise ValueError('revision_conflict: версия сайта изменена')
        from ..author_credit import carry,validate as validate_credit
        data=validate(carry({k:row[k] for k in EDITION_FIELDS if k in row},patch));target=row['archived'] if archived is None else archived
        validate_credit(data.get('author_credit'),data['author'],c=c)
        if data.get('author_credit') and data['author_credit']['site_id']!=row['site_id']:raise ValueError('Сведения об авторе выбраны для другого сайта')
        if all(row.get(k)==data.get(k) for k in EDITION_FIELDS) and target==row['archived']:return row
        row.pop('author_credit',None)
        row.update(data,archived=target,revision=row['revision']+1,updated_at=now());record(c,row);return row


def prepare(edition,site,baseline,adapter):
    """Pure adapter contract: caller chooses whether to persist the returned plan."""
    if edition['site_id']!=site['id']:raise ValueError('Версия относится к другому сайту')
    if edition['archived']:raise ValueError('Версия сайта в архиве')
    if not edition['slug'] or not edition['publication_date'] or not edition['category_id']:raise ValueError('Заполните адрес, дату с часовым поясом и рубрику')
    if adapter.name!=site['adapter']:raise ValueError('Адаптер не соответствует сайту')
    changes=adapter.prepare(edition,site,baseline)
    plan={'schema_version':1,'edition_id':edition['id'],'edition_revision':edition['revision'],'site_id':site['id'],'domain':site['domain'],
          'source_revision':edition['source_revision'],'adapter':adapter.name,'baseline_sha256':baseline['sha256'],'changes':changes,
          'publication_status':'not_published'}
    return {**plan,'package_hash':hashlib.sha256(dump(plan).encode()).hexdigest()}


def validate_plan(plan,edition,site,baseline):
    value={k:v for k,v in plan.items() if k!='package_hash'}
    if hashlib.sha256(dump(value).encode()).hexdigest()!=plan.get('package_hash'):raise ValueError('Пакет выпуска изменён')
    if plan['site_id']!=site['id'] or plan['domain']!=site['domain'] or plan['edition_id']!=edition['id']:raise ValueError('Выпуск относится к другому назначению')
    if plan['edition_revision']!=edition['revision']:raise ValueError('stale_edition: версия сайта изменилась')
    if plan['baseline_sha256']!=baseline['sha256']:raise ValueError('stale_baseline: исходные данные сайта изменились')
    return True
