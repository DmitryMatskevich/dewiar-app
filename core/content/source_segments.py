"""Exact quoted excerpts with explicitly unverified, optional manual times."""
import hashlib
import re
from .store import dump,integer

FIELDS={'id','quote','occurrence','start_us','end_us','speaker_label'}


def normalize(text,items):
    if not isinstance(items,list) or len(items)>200:raise ValueError('Допускается до 200 фрагментов источника')
    result=[];seen=set();end=0;size=0
    for item in items:
        if not isinstance(item,dict) or set(item)!=FIELDS:raise ValueError('Некорректные поля фрагмента')
        ident=item['id'];quote=item['quote'];speaker=item['speaker_label']
        if not isinstance(ident,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',ident) or ident in seen:raise ValueError('ID фрагментов должны быть уникальными')
        if not isinstance(quote,str) or not quote.strip() or len(quote)>6000 or '\x00' in quote:raise ValueError('Укажите точную цитату до 6000 символов')
        if speaker is not None and (not isinstance(speaker,str) or len(speaker)>120 or '\x00' in speaker):raise ValueError('Имя говорящего: до 120 символов либо пусто')
        occurrence=integer(item['occurrence'],'Номер вхождения цитаты',1,1000)
        start=-1
        for _ in range(occurrence):
            start=text.find(quote,start+1)
            if start<0:raise ValueError('Цитата с указанным номером вхождения не найдена в исходном тексте')
        a,b=item['start_us'],item['end_us']
        if (a is None)!=(b is None):raise ValueError('Укажите оба времени или оставьте оба пустыми')
        if a is not None:
            integer(a,'Начало фрагмента',0,86400000000);integer(b,'Конец фрагмента',1,86400000000)
            if a<end or a>=b:raise ValueError('Времена фрагментов должны идти по порядку без пересечений')
            end=b
        size+=len(quote.encode())
        if size>512000:raise ValueError('Цитаты превышают 500 КиБ')
        result.append({**item,'speaker_label':speaker or None});seen.add(ident)
    return result


def annotation(text,items):
    return {'format':1,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'timing_origin':'editor_declared','alignment_verified':False,'media_ref':None,
            'items':normalize(text,items)}


def validate(text,value):
    if not isinstance(value,dict) or set(value)!={'format','text_sha256','timing_origin','alignment_verified','media_ref','items'}:raise ValueError('Некорректная разметка источника')
    if type(value['format']) is not int or value['format']!=1 or value['alignment_verified'] is not False or value['media_ref'] is not None or value['timing_origin'] not in ('editor_declared','package_claimed'):raise ValueError('Нельзя заявлять проверенное выравнивание или медиа для ручной разметки')
    if value['text_sha256']!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('Разметка относится к другому тексту источника')
    if normalize(text,value['items'])!=value['items']:raise ValueError('Разметка источника не нормализована')
    return value


def summary(source):
    value=source.get('segments_v1')
    return {'segment_count':len(value['items']) if value else 0}


def get(store,args):
    from .source_records import records_in
    with store.db() as c:
        row=store._get(c,args.get('id'));revision=args.get('revision',row['revision'])
        record=next((r for r in records_in(c,row['id'],revision) if r['id']==args.get('source_id')),None)
        if record is None:raise ValueError('Источник не входит в выбранную версию статьи')
        source=record['snapshot']
        if source.get('asset_id'):raise ValueError('Разметка доступна для текстовых расшифровок и заметок. Для HTML сначала создайте отдельный текстовый источник.')
        text=source.get('text','');value=source.get('segments_v1')
        if value:validate(text,value)
        return {'source_id':record['id'],'original_text':text,'annotation':value,'items':value['items'] if value else []}
