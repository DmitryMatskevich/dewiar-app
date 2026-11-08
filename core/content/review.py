"""Revision-bound editorial decisions. Source matches are evidence, not truth scores."""
import json
import re
from urllib.parse import urlparse
from .store import dump, now, integer

STATUSES={'needs_review','supported','opinion','disputed'}


def review_blocks(row):
    return [{'id':'$title','type':'heading','text':row['title']},*row['blocks']]


def source_texts(store,mid,revision=None):
    out=[]
    from .source_records import records_in
    with store.db() as c:
        current=store._get(c,mid)
        records=records_in(c,mid,current['revision'] if revision is None else revision)
    for record in records:
        s=record['snapshot']
        text=s.get('text','')
        if s.get('source_kind')=='html':
            from .assets import read
            text=read(store,s['asset_id']).decode('utf-8')
        out.append({'source_id':record['id'],'sha256':s['sha256'],'title':s.get('task_title','Источник'),'text':text})
    return out


def decisions(row,items,sources):
    if not isinstance(items,list) or len(items)!=len(review_blocks(row)):raise ValueError('Нужна проверка заголовка и каждого блока')
    blocks={b['id']:b for b in review_blocks(row)};out=[];seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)-{'source_id'}!={'block_id','status','quote','source_sha256','url','note'}:raise ValueError('Некорректная запись проверки')
        if any(not isinstance(v,str) for v in item.values()) or item['block_id'] not in blocks or item['block_id'] in seen:raise ValueError('Неизвестный или повторный блок проверки')
        seen.add(item['block_id']);b=blocks[item['block_id']];status=item['status']
        if status not in STATUSES or len(item.get('source_id',''))>128 or any(len(item[k])>limit for k,limit in [('quote',6000),('url',2000),('note',2000),('source_sha256',64)]):raise ValueError('Некорректные поля редакционной проверки')
        value={**item,'evidence_kind':None}
        if status=='supported':
            if len(item['quote'].strip())<8:raise ValueError('Для подтверждения укажите выдержку из источника (от 8 символов)')
            if item['url']:
                u=urlparse(item['url'])
                if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise ValueError('Нужна ссылка HTTP/HTTPS без учётных данных')
                value['evidence_kind']='editor_declared_external'
            else:
                matches=[s for s in sources if (s['source_id']==item['source_id'] if item.get('source_id') else s['sha256']==item['source_sha256'])]
                if len(matches)>1:raise ValueError('Выберите точный источник: совпадают контрольные суммы разных материалов')
                source=matches[0] if matches else None
                if not source or item['quote'] not in source['text']:raise ValueError('Выдержка не найдена в выбранном сохранённом источнике')
                if source['sha256']!=item['source_sha256']:raise ValueError('Контрольная сумма выбранного источника не совпала')
                value['source_id']=source['source_id']
                value['evidence_kind']='snapshot_quote_verified'
            # Conservative lexical aid only; no claim that a quote entails the claim.
            missing=[n for n in numbers(b['text']) if n not in numbers(item['quote'])]
            if missing:raise ValueError('В выдержке отсутствуют числа блока: '+', '.join(missing[:20]))
        elif status=='opinion':
            if len(item['note'].strip())<12:raise ValueError('Для авторского суждения укажите редакторское пояснение')
            if numbers(b['text']):raise ValueError('Блок с числами требует проверки источника; отделите факты от мнения')
        out.append(value)
    return out


def numbers(text):
    # Dates, ranges and units still need human review; these tokens are only cues.
    return sorted(set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?',text)))


def evaluate(row,review):
    existing={d['block_id']:d for d in review.get('decisions',[])};claims=[];issues=[]
    if not row['author'].strip():issues.append('Не указан автор')
    if row['archived']:issues.append('Материал в архиве')
    for b in review_blocks(row):
        d=existing.get(b['id'],{});status=d.get('status','needs_review')
        claims.append({'block_id':b['id'],'text':b['text'],'numbers':numbers(b['text']),'status':status,**d})
        if b['type']=='unsupported':issues.append('Неподдерживаемый HTML: '+b['id'])
        if not b['text'].strip():issues.append('Пустой блок: '+b['id'])
        if status not in ('supported','opinion'):issues.append('Не завершена проверка блока: '+b['id'])
    return {'claims':claims,'issues':issues,'ready':bool(review.get('ready') and review.get('reviewer') and not issues)}


def report(store,mid):
    row=store.get(mid)
    with store.db() as c:r=c.execute('SELECT snapshot FROM editorial_reviews WHERE material_id=? AND revision=?',(mid,row['revision'])).fetchone()
    review=json.loads(r['snapshot']) if r else {'review_version':0,'reviewer':'','decisions':[],'ready':False}
    return {'revision':row['revision'],'review':review,**evaluate(row,review),
            'sources':[{k:s[k] for k in ('source_id','sha256','title')} for s in source_texts(store,mid,row['revision'])],
            'note':'Совпадение цитаты и чисел не доказывает смысловую достоверность. Решение принимает редактор. Внешние источники отмечены со слов редактора.'}


def save(store,args):
    mid=args.get('id');expected=integer(args.get('expected_revision'),'Ревизия',1)
    rv=integer(args.get('expected_review_version'),'Версия проверки')
    reviewer=args.get('reviewer');ready=args.get('ready',False)
    if not isinstance(reviewer,str) or not 1<=len(reviewer.strip())<=240 or type(ready) is not bool:raise ValueError('Укажите редактора проверки')
    sources=source_texts(store,mid,expected)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=store._get(c,mid)
        if row['revision']!=expected:raise ValueError('revision_conflict: проверяется другая версия текста')
        previous=c.execute('SELECT review_version FROM editorial_reviews WHERE material_id=? AND revision=?',(mid,expected)).fetchone()
        if (previous['review_version'] if previous else 0)!=rv:raise ValueError('review_conflict: редакционная проверка уже изменена')
        normalized=decisions(row,args.get('decisions'),sources)
        review={'reviewer':reviewer.strip(),'review_version':rv+1,'decisions':normalized,'ready':ready,'saved_at':now()}
        issues=evaluate(row,review)['issues']
        if ready and issues:raise ValueError('Материал не готов: '+'; '.join(issues[:10]))
        c.execute('INSERT INTO editorial_reviews VALUES (?,?,?,?) ON CONFLICT(material_id,revision) DO UPDATE SET review_version=excluded.review_version,snapshot=excluded.snapshot',(mid,expected,rv+1,dump(review)))
    return report(store,mid)
