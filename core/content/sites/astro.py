"""DCNT/OCKC data adapter. Reads source, prepares one record; never writes the site."""
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from ..store import dump
from ..models import body_from_blocks, render_html


def capture(site,root):
    if site['id'] not in ('site-dcnt-ru','site-ockc-ru') or site['adapter']!='astro':raise ValueError('Для этого сайта Astro-адаптер не проверен')
    root=Path(root).expanduser().resolve()
    files={}
    for relative in ('src/data/pages.json','src/data/categories.json','astro.config.mjs'):
        path=root/relative
        if not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError('Не найден или слишком большой файл Astro: '+relative)
        files[relative]=path.read_bytes()
    config=files['astro.config.mjs'].decode('utf-8')
    declared=re.search(r"\bsite\s*:\s*['\"](https://[^'\"]+)['\"]",config)
    if not declared or declared[1].rstrip('/')!='https://'+site['domain']:raise ValueError('Домен astro.config.mjs не совпадает с выбранным сайтом')
    try:pages=json.loads(files['src/data/pages.json']);categories=json.loads(files['src/data/categories.json'])
    except ValueError as e:raise ValueError('Некорректный JSON исходных данных Astro') from e
    if not isinstance(pages,list) or not isinstance(categories,list) or any(not isinstance(x,dict) for x in pages+categories):raise ValueError('Неизвестная схема Astro')
    hashes={k:hashlib.sha256(v).hexdigest() for k,v in files.items()}
    for path in sorted((root/'src').rglob('*')):
        if path.is_file():
            relative=str(path.relative_to(root))
            if relative not in hashes:hashes[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
    return {'root':str(root),'pages':pages,'categories':categories,'hashes':hashes,'sha256':hashlib.sha256(dump({'root':str(root),'files':hashes}).encode()).hexdigest()}


class AstroAdapter:
    name='astro'

    def prepare(self,edition,site,baseline):
        if edition['language']!='ru':raise ValueError('Текущий шаблон этого сайта проверен для русского языка')
        categories={str(c.get('id')):c for c in baseline['categories']}
        category=categories.get(edition['category_id'])
        if not category:raise ValueError('Рубрика отсутствует в исходных данных сайта')
        path='/articles/'+edition['slug']+'/';rid='dewiar-'+edition['id']
        owned=[p for p in baseline['pages'] if p.get('id')==rid]
        if len(owned)>1:raise ValueError('Дублирующий ID записи в данных сайта')
        if any(p.get('proposedUrl')==path and p.get('id')!=rid for p in baseline['pages']):raise ValueError('Этот URL уже занят другой страницей')
        previous=owned[0] if owned else None
        if previous and (previous.get('dewiarEditionId')!=edition['id'] or previous.get('dewiarMaterialId')!=edition['material_id']):raise ValueError('Существующая запись принадлежит другому материалу')
        if previous and previous.get('proposedUrl')!=path:raise ValueError('Изменение опубликованного адреса требует отдельного redirect-плана')
        date=datetime.fromisoformat(edition['publication_date']).astimezone(ZoneInfo('Europe/Moscow'))
        rendered=render_html(edition['blocks'])
        if site['id']=='site-ockc-ru':
            rendered=re.sub(r'<(p|h2|blockquote|li)>',r'<\1 style="word-break:normal;overflow-wrap:break-word;hyphens:none">',rendered)
        fragment='<p class="legacy-note"><time datetime="'+html.escape(date.isoformat(),quote=True)+'">'+date.strftime('%d.%m.%Y')+'</time> · Автор: '+html.escape(edition['author'])+'</p>\n'+rendered
        bio=(edition.get('author_credit') or {}).get('bio','')
        if bio:fragment+='\n<aside class="article-author-bio"><h2>Об авторе</h2><p style="white-space:pre-wrap">'+html.escape(bio)+'</p></aside>'
        start='<!-- dewiar:body:'+edition['id']+':start -->';end='<!-- dewiar:body:'+edition['id']+':end -->'
        managed=start+'\n'+fragment+'\n'+end
        preserved=managed
        if previous:
            old=previous.get('preservedHtml','')
            if old.count(start)!=1 or old.count(end)!=1 or old.index(start)>old.index(end):raise ValueError('Не найден единственный управляемый фрагмент; старый HTML сохранён')
            preserved=old[:old.index(start)]+managed+old[old.index(end)+len(end):]
        record={**(previous or {'origin':'native','contentLinks':[],'mediaEmbeds':[],'images':[],'commentCount':0}),
                'id':rid,'dewiarMaterialId':edition['material_id'],'dewiarEditionId':edition['id'],'dewiarEditionRevision':edition['revision'],
                'sourceUrl':'https://'+site['domain']+path,'title':edition['title'],'h1':edition['title'],'description':edition['description'],
                'collection':'posts','proposedUrl':path,'previewText':body_from_blocks(edition['blocks'])[:500],
                'postDate':date.strftime('%Y-%m-%d %H:%M:%S'),'categoryId':edition['category_id'],'categoryName':str(category.get('title') or ''),
                'preservedHtml':preserved}
        if site['id']=='site-dcnt-ru':record['wholeWordWrap']=True
        if site['id']=='site-ockc-ru':record['categoryIds']=[edition['category_id']]
        return {'operation':'update' if previous else 'create','record':record,'path':path,'category_path':'/category/'+edition['category_id']+'/',
                'previous_record_sha256':hashlib.sha256(dump(previous).encode()).hexdigest() if previous else None}


def patched_pages(plan,baseline):
    # Called only after validate_plan; a pure copy preserves every neighbouring row.
    record=plan['changes']['record'];rid=record['id'];before=baseline['pages']
    if plan['changes']['operation']=='create':return [*before,record]
    found=[p for p in before if p.get('id')==rid]
    if len(found)!=1 or hashlib.sha256(dump(found[0]).encode()).hexdigest()!=plan['changes']['previous_record_sha256']:raise ValueError('stale_baseline: изменена целевая запись')
    return [record if p.get('id')==rid else p for p in before]
