"""Local screenshot annotations. One atomic state; exports are revision checked."""
import base64
import hashlib
import io
import json
import math
import os
import re
import threading
import uuid
from pathlib import Path
from PIL import Image
from visual_compose import compose
LOCK = threading.RLock()
TYPES = {'FrameMarker','EllipseFrameMarker','ArrowMarker','LineMarker','FreehandMarker','HighlighterMarker','TextMarker','CoverMarker'}

def atomic(path, data):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_bytes(data); os.replace(tmp,path)
    finally: tmp.unlink(missing_ok=True)

def save(path, value):
    atomic(path,json.dumps(value,ensure_ascii=False,allow_nan=False).encode())

def locate(engine, identifier):
    item=engine.chatlog.screenshot(identifier)
    att=item['att'][0]
    src=engine.workspace.safe(att['path'])
    folder=src.with_suffix('.разметка')
    folder.mkdir(exist_ok=True)
    return item,src,folder

def read(engine, identifier):
    item,src,folder=locate(engine,identifier)
    digest=hashlib.sha256(src.read_bytes()).hexdigest()
    path=folder/'state.json'
    if path.exists():
        doc=json.loads(path.read_text())
        if doc['source']['sha256']!=digest:raise ValueError('Оригинал изменён; разметку нельзя применить')
    else:
        with Image.open(src) as im:w,h=im.size
        if w*h>50_000_000:raise ValueError('Снимок больше 50 мегапикселей')
        doc={'schema':1,'asset_id':identifier,'revision':0,'crop':None,'editor_version':'3.10.0',
             'source':{'sha256':digest,'width':w,'height':h},'state':{'version':3,'width':w,'height':h,'markers':[]}}
        save(path,doc)
    return item,src,folder,doc

def validate(state,source,crop):
    if not isinstance(state,dict) or len(json.dumps(state,allow_nan=False).encode())>4_000_000:raise ValueError('Слишком большая разметка')
    if (state.get('width'),state.get('height'))!=(source['width'],source['height']):raise ValueError('Размер разметки не совпадает со снимком')
    markers=state.get('markers')
    if not isinstance(markers,list) or len(markers)>500:raise ValueError('Допускается до 500 пометок')
    limit=max(source['width'],source['height'])*4
    def walk(v,depth=0):
        if depth>16:raise ValueError('Слишком вложенные данные')
        if isinstance(v,float) and not math.isfinite(v):raise ValueError('Некорректное число')
        if isinstance(v,(int,float)) and abs(v)>max(limit,20000):raise ValueError('Значение вне границ')
        if isinstance(v,str) and (len(v)>20000 or '<script' in v.lower()):raise ValueError('Слишком длинное значение')
        if isinstance(v,list):
            if len(v)>20000:raise ValueError('Слишком длинный штрих')
            for x in v:walk(x,depth+1)
        if isinstance(v,dict):
            for x in v.values():walk(x,depth+1)
    for m in markers:
        if not isinstance(m,dict) or m.get('typeName') not in TYPES:raise ValueError('Неизвестный инструмент')
        if len(m.get('text',''))>2000:raise ValueError('Подпись длиннее 2000 знаков')
        walk(m)
        for k in ('left','top','width','height','x1','x2','y1','y2','rotationAngle','strokeWidth','opacity'):
            if k in m and (isinstance(m[k],bool) or not isinstance(m[k],(int,float))):raise ValueError('Неверная координата')
        if any(m.get(k,0)<0 for k in ('width','height','strokeWidth')):raise ValueError('Отрицательный размер')
        if m.get('strokeWidth',0)>200:raise ValueError('Слишком толстая линия')
        for k in ('fillColor','strokeColor','color'):
            if k in m and not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?|transparent|rgba?\([\d., %]+\)',str(m[k])):raise ValueError('Неверный цвет')
    if crop is not None:
        if not isinstance(crop,dict) or any(type(crop.get(k)) is not int for k in ('x','y','width','height')):raise ValueError('Неверная обрезка')
        if crop['x']<0 or crop['y']<0 or crop['width']<2 or crop['height']<2 or crop['x']+crop['width']>source['width'] or crop['y']+crop['height']>source['height']:raise ValueError('Обрезка вне снимка')

def exported(folder,doc):
    path=folder/'export.png'; meta=folder/'export.json'
    if not path.exists() or not meta.exists():raise ValueError('Откройте карандаш и подготовьте PNG')
    m=json.loads(meta.read_text())
    if m['revision']!=doc['revision'] or m['sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('Пометки изменились. Подготовьте PNG в редакторе')
    return path

def checkpoint(folder,doc):
    hist=folder/'history';hist.mkdir(exist_ok=True);save(hist/f'{doc["revision"]:08d}.json',doc)
    for old in sorted(hist.glob('*.json'))[:-20]:old.unlink()

def command(engine,cmd,a):
    if cmd in ('visual_dictation_start','visual_dictation_finish','visual_dictation_cancel'):
        if cmd=='visual_dictation_start':
            with LOCK:read(engine,str(a.get('asset_id') or ''))
        from visual_dictation import command as dictate
        return dictate(engine,cmd,a)
    if cmd=='visual_command':
        with LOCK:
            _,src,folder,doc=read(engine,str(a.get('asset_id') or ''))
            if a.get('base_revision')!=doc['revision']:raise ValueError('Пометки изменились. Повторите команду.')
        # Native OCR may take seconds; don't block autosave in other editor windows.
        from visual_commands import plan
        result=plan(src,folder,doc,a.get('text'))
        with LOCK:
            if read(engine,str(a.get('asset_id') or ''))[3]['revision']!=doc['revision']:raise ValueError('Пометки изменились во время распознавания. Повторите команду.')
        return result
    with LOCK:
        identifier=str(a.get('asset_id') or a.get('id') or '')
        item,src,folder,doc=read(engine,identifier)
        if cmd=='visual_tasks':return [{'id':t['id'],'title':t['title']} for t in engine.tasks.list()]
        if cmd=='visual_checkpoint':
            checkpoint(folder,doc);return True
        if cmd=='visual_open':return doc
        if cmd=='visual_source':return 'data:image/png;base64,'+base64.b64encode(src.read_bytes()).decode()
        if cmd=='visual_save':
            if a.get('base_revision')!=doc['revision']:raise ValueError('Конфликт: снимок изменён в другом окне. Сохраните свои правки отдельно.')
            state,crop=a.get('state'),a.get('crop')
            validate(state,doc['source'],crop)
            if (state,crop)!=(doc['state'],doc['crop']):
                doc.update(state=state,crop=crop,revision=doc['revision']+1)
                save(folder/'state.json',doc)
                markup=dict(item['att'][0].get('capture',{}).get('markup') or {})
                markup.update(asset_id=identifier,revision=doc['revision'])
                engine.chatlog.screenshot(identifier,markup=markup)
                engine.emit('visual_changed',id=identifier,markup=markup)
            return {'revision':doc['revision']}
        if cmd=='visual_publish':
            if a.get('revision')!=doc['revision']:raise ValueError('Разметка изменилась во время экспорта. Повторите.')
            value=a.get('overlay','')
            if not isinstance(value,str) or len(value)>12_000_000 or not value.startswith('data:image/png;base64,'):raise ValueError('Слой слишком большой или не PNG (предел 12 МБ)')
            raw=base64.b64decode(value.split(',',1)[1],validate=True)
            result,info=compose(src.read_bytes(),raw,doc['state'],doc['crop'])
            atomic(folder/'export.png',result)
            save(folder/'export.json',{'revision':doc['revision'],'sha256':hashlib.sha256(result).hexdigest(),**info})
            checkpoint(folder,doc)
            with Image.open(io.BytesIO(result)) as im:
                im.thumbnail((320,240));b=io.BytesIO();im.convert('RGB').save(b,'JPEG',quality=85)
            markup={'asset_id':identifier,'revision':doc['revision'],'export_revision':doc['revision'],'thumb':'data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode()}
            engine.chatlog.screenshot(identifier,markup=markup)
            engine.emit('visual_changed',id=identifier,markup=markup);engine.emit('chat_changed')
            return {'revision':doc['revision'],**info}
        path=exported(folder,doc)
        if cmd=='visual_to_canvas':
            from canvases import from_screenshot
            return from_screenshot(engine,a.get('canvas_id'),path.read_bytes(),identifier,src.stem)
        if cmd=='visual_copy':
            from macsys import copy_png_file
            return copy_png_file(path)
        if cmd=='visual_save_as':
            dest=engine.workspace.safe('Изображения/'+src.stem+' — с пометками '+uuid.uuid4().hex[:6]+'.png')
            dest.parent.mkdir(parents=True,exist_ok=True);atomic(dest,path.read_bytes());return {'path':str(dest)}
        if cmd=='visual_to_note':
            rel=engine.workspace.save_note(src.stem+' — с пометками','',folder='Снимки экрана')
            att=engine.workspace.note_attach(rel,'Снимок с пометками.png',path.read_bytes())
            engine.workspace.write(rel,att['line']+'\n')
            engine.emit('note_saved',kind='note',title=src.stem,path=rel,text='Снимок с пометками')
            return {'path':rel}
        if cmd=='visual_to_task':
            tid=a.get('task_id')
            task=engine.tasks.get(tid) if tid else engine.tasks.create(src.stem+' — пометки',a.get('text') or 'Рассмотреть приложенный снимок с пометками',start=False)
            dest=task.dir/'inputs'/('Снимок-'+uuid.uuid4().hex[:8]+'.png');dest.parent.mkdir(exist_ok=True);atomic(dest,path.read_bytes())
            engine.tasks.tell(task.id, (a.get('text') or 'Снимок с пометками')+'\nФайл: inputs/'+dest.name)
            return {'task_id':task.id,'path':str(dest)}
        raise ValueError('Неизвестная команда редактора')
