"""Scene citations point only at exact source records pinned to this video."""
import json


def validate(c,value,allowed,cache=None):
    if not isinstance(value,list) or len(value)>20:raise ValueError('У сцены может быть до 20 ссылок на фрагменты')
    seen=set();result=[]
    if cache is None:cache={}
    for ref in value:
        if not isinstance(ref,dict) or set(ref)!={'source_id','segment_id'} or any(not isinstance(x,str) for x in ref.values()):raise ValueError('Некорректная ссылка на фрагмент сцены')
        key=(ref['source_id'],ref['segment_id'])
        if key in seen:raise ValueError('Фрагмент уже привязан к сцене')
        if ref['source_id'] not in allowed:raise ValueError('Источник не закреплён в этом видео')
        if ref['source_id'] not in cache:
            record=c.execute('SELECT snapshot FROM content_source_records WHERE id=?',(ref['source_id'],)).fetchone()
            if record is None:raise ValueError('Источник фрагмента недоступен')
            snapshot=json.loads(record['snapshot']);annotation=snapshot.get('segments_v1')
            if annotation:
                from .source_segments import validate as validate_annotation
                validate_annotation(snapshot.get('text',''),annotation)
            cache[ref['source_id']]={x['id'] for x in annotation['items']} if annotation else set()
        if ref['segment_id'] not in cache[ref['source_id']]:raise ValueError('Фрагмент не найден в закреплённом источнике')
        result.append(dict(ref));seen.add(key)
    return result
