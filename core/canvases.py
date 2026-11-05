"""Local Excalidraw documents. Scenes are small JSON; raster assets are immutable files.

Uploads follow the bounded offset protocol used by songs, with SHA256 and a purpose.
Neither opening a canvas nor importing a diagram starts an AI model.
"""
import base64
import hashlib
import io
import json
import math
import re
import threading
import time
import uuid
from PIL import Image, ImageOps
from visual_assets import atomic, save

LOCK=threading.RLock()
JOBS={}
LIMIT=64*1024*1024
TYPES={'rectangle','ellipse','diamond','text','freedraw','image','arrow','line','frame'}
APP_KEYS={'viewBackgroundColor','gridSize','gridStep','gridModeEnabled','currentItemStrokeColor','currentItemBackgroundColor','currentItemFillStyle','currentItemStrokeWidth','currentItemStrokeStyle','currentItemRoughness','currentItemOpacity','currentItemFontFamily','currentItemFontSize','currentItemTextAlign','currentItemStartArrowhead','currentItemEndArrowhead','currentItemRoundness'}


def root(engine):
    path=engine.workspace.safe('Холсты');path.mkdir(exist_ok=True);return path


def read(engine,identifier):
    if not re.fullmatch(r'[0-9a-f]{32}',str(identifier)):raise ValueError('Неверный холст')
    folder=root(engine)/identifier
    if not (folder/'state.json').is_file():raise ValueError('Холст не найден')
    return folder,json.loads((folder/'state.json').read_text())


def create(engine,title='Новый холст'):
    identifier=uuid.uuid4().hex;folder=root(engine)/identifier;folder.mkdir()
    doc={'schema':1,'id':identifier,'title':str(title)[:120],'revision':0,'updated':time.time(),'editor':'excalidraw-0.18.1','elements':[],
         'appState':{'viewBackgroundColor':'#ffffff'},'libraryItems':[],'files':{},'sources':[]}
    save(folder/'state.json',doc);return doc


def validate(elements,app,library,files):
    if not isinstance(elements,list) or len(elements)>2000:raise ValueError('Допускается до 2000 объектов')
    if not isinstance(app,dict) or not isinstance(library,list) or len(library)>200:raise ValueError('Неверные настройки холста/библиотеки')
    data={'elements':elements,'appState':app,'libraryItems':library}
    if len(json.dumps(data,allow_nan=False).encode())>4_000_000:raise ValueError('Сцена превышает 4 МБ')
    def walk(value,depth=0):
        if depth>20:raise ValueError('Слишком вложенная сцена')
        if isinstance(value,(float,int)) and (not math.isfinite(value) or abs(value)>10**16):raise ValueError('Некорректное число')
        if isinstance(value,str) and len(value)>50000:raise ValueError('Слишком длинный текст')
        if isinstance(value,list):
            if len(value)>100000:raise ValueError('Слишком длинный штрих')
            for item in value:walk(item,depth+1)
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('__proto__','constructor','prototype'):raise ValueError('Недопустимое поле')
                walk(item,depth+1)
    walk(data)
    def rows(values):
        ids=set()
        for item in values:
            if not isinstance(item,dict) or item.get('type') not in TYPES:raise ValueError('Тип объекта не поддерживается на локальном холсте')
            identifier=item.get('id')
            if not isinstance(identifier,str) or not re.fullmatch(r'[\w-]{1,100}',identifier) or identifier in ids:raise ValueError('Неверный или повторный ID объекта')
            ids.add(identifier)
            for key in ('x','y','width','height','angle'):
                if type(item.get(key)) not in (int,float) or abs(item[key])>1_000_000:raise ValueError('Геометрия за границами холста')
            if item['width']<0 or item['height']<0:raise ValueError('Отрицательный размер')
            if item.get('link'):raise ValueError('Внешние ссылки объектов на локальном холсте отключены')
            if item['type']=='image' and item.get('fileId') not in files:raise ValueError('Изображение ещё не сохранено')
    rows(elements)
    for item in library:
        if not isinstance(item,dict) or not isinstance(item.get('elements'),list):raise ValueError('Неверный элемент библиотеки')
        rows(item['elements'])
    return {k:v for k,v in app.items() if k in APP_KEYS}


def add_image(folder,doc,data,name='Изображение',file_id=None):
    if not 0<len(data)<=LIMIT:raise ValueError('Изображение больше 64 МиБ')
    with Image.open(io.BytesIO(data)) as im:
        if im.format not in ('PNG','JPEG','WEBP'):raise ValueError('Нужен PNG, JPEG или WebP')
        if im.width*im.height>50_000_000:raise ValueError('Изображение больше 50 мегапикселей')
        im=ImageOps.exif_transpose(im).convert('RGBA');im.info.clear();b=io.BytesIO();im.save(b,'PNG');normalized=b.getvalue();w,h=im.size
    digest=hashlib.sha256(normalized).hexdigest();identifier=file_id or digest
    if not re.fullmatch(r'[\w-]{1,100}',str(identifier)):raise ValueError('Неверный ID изображения')
    if identifier in doc['files']:
        if doc['files'][identifier]['sha256']!=digest:raise ValueError('ID изображения уже занят другим файлом')
        return doc['files'][identifier]
    if len(doc['files'])>=100 or sum(f['size']+f.get('original_size',f['size']) for f in doc['files'].values())+len(normalized)+len(data)>256*1024*1024:raise ValueError('Холст ограничен 100 изображениями и 256 МиБ')
    assets=folder/'assets';assets.mkdir(exist_ok=True)
    path=assets/(digest+'.png');atomic(path,normalized)
    original=assets/('original-'+hashlib.sha256(data).hexdigest());atomic(original,data)
    item={'id':identifier,'path':str(path.relative_to(folder)),'original':str(original.relative_to(folder)),'sha256':digest,'size':len(normalized),'original_size':len(data),'mimeType':'image/png','created':int(time.time()*1000),'width':w,'height':h,'name':str(name)[:120]}
    doc['files'][identifier]=item;save(folder/'state.json',doc);return item


def file_data(folder,item):
    return {'id':item['id'],'mimeType':item['mimeType'],'created':item['created'],'dataURL':'data:image/png;base64,'+base64.b64encode((folder/item['path']).read_bytes()).decode()}


def image_element(item,x=0,y=0):
    return {'id':uuid.uuid4().hex,'type':'image','x':x,'y':y,'width':item['width'],'height':item['height'],'angle':0,
            'strokeColor':'transparent','backgroundColor':'transparent','fillStyle':'solid','strokeWidth':1,'strokeStyle':'solid','roughness':0,'opacity':100,
            'groupIds':[],'frameId':None,'roundness':None,'seed':1,'version':1,'versionNonce':1,'isDeleted':False,'boundElements':None,'updated':int(time.time()*1000),'link':None,'locked':False,
            'fileId':item['id'],'status':'saved','scale':[1,1],'crop':None}


def from_screenshot(engine,identifier,data,screenshot_id,title):
    with LOCK:
        doc=create(engine,title) if not identifier else read(engine,identifier)[1]
        folder,doc=read(engine,doc['id']);item=add_image(folder,doc,data,title)
        bottom=max((el['y']+el['height'] for el in doc['elements'] if not el.get('isDeleted')),default=-80)
        doc['elements'].append(image_element(item,0,bottom+80));doc['sources'].append({'screenshot_id':screenshot_id,'title':title})
        validate(doc['elements'],doc['appState'],doc['libraryItems'],doc['files'])
        doc['revision']+=1;doc['updated']=time.time();save(folder/'state.json',doc)
        engine.emit('canvas_changed',id=doc['id'],revision=doc['revision']);return {'canvas_id':doc['id']}


def exported(folder,doc):
    path=folder/'export.png';meta=folder/'export.json'
    if not path.exists() or not meta.exists():raise ValueError('Подготовьте PNG на холсте')
    row=json.loads(meta.read_text())
    if row['revision']!=doc['revision'] or row['sha256']!=hashlib.sha256(path.read_bytes()).hexdigest():raise ValueError('PNG устарел. Подготовьте его заново.')
    return path


def upload(engine,cmd,args):
    now=time.monotonic();workspace=str(root(engine))
    for token,job in list(JOBS.items()):
        if now-job['at']>600:job['path'].unlink(missing_ok=True);JOBS.pop(token)
    folder,doc=read(engine,args.get('canvas_id'))
    if cmd=='canvas_upload_begin':
        size=args.get('size');purpose=args.get('purpose')
        if type(size) is not int or not 0<size<=LIMIT or purpose not in ('image','export'):raise ValueError('Файл не больше 64 МиБ')
        if len(JOBS)>=4:raise ValueError('Дождитесь завершения загрузки')
        if not re.fullmatch('[a-f0-9]{64}',str(args.get('sha256'))):raise ValueError('Нужна SHA256 файла')
        if purpose=='export' and args.get('revision')!=doc['revision']:raise ValueError('Холст изменился до экспорта')
        temp=folder/'uploads';temp.mkdir(exist_ok=True)
        for old in temp.glob('*.part'):
            if time.time()-old.stat().st_mtime>600:old.unlink(missing_ok=True)
        token=uuid.uuid4().hex;path=temp/(token+'.part');path.touch()
        JOBS[token]={'workspace':workspace,'canvas_id':doc['id'],'path':path,'size':size,'received':0,'at':now,'args':dict(args)}
        return {'upload_id':token,'offset':0}
    token=args.get('upload_id');job=JOBS.get(token)
    if not job or job['workspace']!=workspace or job['canvas_id']!=doc['id']:raise ValueError('Загрузка истекла или относится к другому холсту')
    if cmd=='canvas_upload_cancel':job['path'].unlink(missing_ok=True);JOBS.pop(token);return True
    if cmd=='canvas_upload_chunk':
        if args.get('offset')!=job['received']:raise ValueError('Неверный порядок частей')
        value=args.get('data')
        if not isinstance(value,str) or len(value)>750000:raise ValueError('Слишком большая часть')
        data=base64.b64decode(value,validate=True)
        if not data or job['received']+len(data)>job['size']:raise ValueError('Размер файла не совпадает')
        with job['path'].open('ab') as stream:stream.write(data)
        job.update(received=job['received']+len(data),at=now);return {'offset':job['received']}
    if cmd!='canvas_upload_finish':raise ValueError('Неизвестная команда загрузки')
    if job['received']!=job['size']:raise ValueError('Загрузка не завершена')
    try:
        data=job['path'].read_bytes();options=job['args']
        if hashlib.sha256(data).hexdigest()!=options['sha256']:raise ValueError('Контрольная сумма не совпадает')
        if options['purpose']=='image':return add_image(folder,doc,data,options.get('name','Изображение'),options.get('file_id'))
        if options['revision']!=doc['revision']:raise ValueError('Холст изменился во время экспорта')
        with Image.open(io.BytesIO(data)) as im:
            if im.format!='PNG' or im.width*im.height>50_000_000:raise ValueError('Нужен PNG до 50 мегапикселей')
            im.load();clean=im.convert('RGBA');clean.info.clear();out=io.BytesIO();clean.save(out,'PNG')
        data=out.getvalue();atomic(folder/'export.png',data);save(folder/'export.json',{'revision':doc['revision'],'sha256':hashlib.sha256(data).hexdigest()})
        history=folder/'history';history.mkdir(exist_ok=True);save(history/f'{doc["revision"]:08d}.json',{k:v for k,v in doc.items() if k!='files'})
        for old in sorted(history.glob('*.json'))[:-20]:old.unlink()
        return {'width':clean.width,'height':clean.height}
    finally:job['path'].unlink(missing_ok=True);JOBS.pop(token,None)


def command(engine,cmd,a):
    if cmd.startswith('canvas_chat_'):
        from canvas_chat import command as chat_command
        return chat_command(engine,cmd,a)
    with LOCK:
        if cmd=='canvas_create':return create(engine,a.get('title') or 'Новый холст')
        if cmd=='canvas_list':
            rows=[]
            for path in root(engine).glob('*/state.json'):
                try:
                    row=json.loads(path.read_text());rows.append({k:row[k] for k in ('id','title','updated')})
                except (ValueError,OSError,KeyError):continue
            return sorted(rows,key=lambda row:-row['updated'])[:200]
        if cmd.startswith('canvas_upload_'):return upload(engine,cmd,a)
        folder,doc=read(engine,a.get('canvas_id'))
        if cmd=='canvas_open':return doc
        if cmd=='canvas_file':return file_data(folder,doc['files'][a['file_id']])
        if cmd=='canvas_save':
            if a.get('base_revision')!=doc['revision']:raise ValueError('Холст изменён в другом окне. Перезагрузите после сохранения своей копии.')
            state=a.get('scene',{});elements=state.get('elements');app=state.get('appState');library=state.get('libraryItems',[])
            app=validate(elements,app,library,doc['files']);title=str(a.get('title',doc['title'])).strip()[:120] or 'Холст'
            if (doc['elements'],doc['appState'],doc['libraryItems'],doc['title'])!=(elements,app,library,title):
                doc.update(elements=elements,appState=app,libraryItems=library,title=title,revision=doc['revision']+1,updated=time.time());save(folder/'state.json',doc)
            return {'revision':doc['revision']}
        if cmd=='canvas_tasks':return [{'id':row['id'],'title':row['title']} for row in engine.tasks.list()]
        if cmd=='canvas_document':
            used={el.get('fileId') for el in doc['elements'] if not el.get('isDeleted')}
            data={'type':'excalidraw','version':2,'source':'Dewiar','elements':[el for el in doc['elements'] if not el.get('isDeleted')],'appState':doc['appState'],'files':{key:file_data(folder,item) for key,item in doc['files'].items() if key in used}}
            path=engine.workspace.safe('Изображения/Холст-'+doc['id'][:8]+'.excalidraw');path.parent.mkdir(exist_ok=True);save(path,data);return {'path':str(path)}
        path=exported(folder,doc)
        if cmd=='canvas_copy':
            from macsys import copy_png_file
            return copy_png_file(path)
        if cmd=='canvas_save_as':
            dest=engine.workspace.safe('Изображения/Холст-'+uuid.uuid4().hex[:8]+'.png');dest.parent.mkdir(exist_ok=True);atomic(dest,path.read_bytes());return {'path':str(dest)}
        if cmd=='canvas_to_note':
            rel=engine.workspace.save_note(doc['title'],'',folder='Холсты');att=engine.workspace.note_attach(rel,'Холст.png',path.read_bytes());engine.workspace.write(rel,att['line']+'\n')
            engine.emit('note_saved',kind='note',title=doc['title'],path=rel,text='Холст');return {'path':rel}
        if cmd=='canvas_to_task':
            text=str(a.get('text') or 'Рассмотреть схему');task=engine.tasks.get(a['task_id']) if a.get('task_id') else engine.tasks.create(doc['title'],text,start=False)
            dest=task.dir/'inputs'/('Холст-'+uuid.uuid4().hex[:8]+'.png');dest.parent.mkdir(exist_ok=True);atomic(dest,path.read_bytes());engine.tasks.tell(task.id,text+'\nФайл: inputs/'+dest.name);return {'task_id':task.id}
        raise ValueError('Неизвестная команда холста')
