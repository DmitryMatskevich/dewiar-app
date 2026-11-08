"""Manual subtitle tracks bound to a verified render, never inferred alignment."""
import base64
import hashlib
import html
import json
import re
import uuid
from .store import dump,integer,now

COMMANDS={'content_caption_get','content_caption_save','content_caption_archive','content_caption_parse','content_caption_export','content_caption_preview'}

def migrate(c):
    c.execute('CREATE TABLE content_captions(id TEXT PRIMARY KEY,import_id TEXT NOT NULL REFERENCES content_video_imports(id),language TEXT NOT NULL,revision INTEGER NOT NULL,snapshot TEXT NOT NULL,UNIQUE(import_id,language))')
    c.execute('CREATE TABLE content_caption_revisions(id TEXT NOT NULL REFERENCES content_captions(id),revision INTEGER NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(id,revision))')

def language(value):
    if value not in ('ru','en'):raise ValueError('Выберите язык ru или en')
    return value

def _media(c,ident):
    if not isinstance(ident,str):raise ValueError('Укажите результат импорта видео')
    row=c.execute('SELECT * FROM content_video_imports WHERE id=?',(ident,)).fetchone()
    if not row or row['state']!='imported':raise ValueError('Нужен успешно принятый видеофайл')
    receipt=json.loads(row['receipt'])
    if receipt.get('full_decode')!='passed':raise ValueError('Нет подтверждения полного декодирования файла')
    return receipt

def cues(value,duration):
    if not isinstance(value,list) or not 1<=len(value)<=2000:raise ValueError('Допускается от 1 до 2000 субтитров')
    result=[];seen=set();end=0;size=0
    for index,cue in enumerate(value,1):
        if not isinstance(cue,dict) or set(cue)!={'id','start_us','end_us','text'}:raise ValueError('Некорректная запись субтитра')
        ident=cue['id']
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',ident) or ident in seen:raise ValueError('ID субтитров должны быть уникальны')
        start=integer(cue['start_us'],'Начало',0,86400000000);stop=integer(cue['end_us'],'Конец',1,86400000000)
        if start%1000 or stop%1000:raise ValueError('Точность субтитров — целые миллисекунды')
        if start<end or start>=stop or stop>duration:raise ValueError(f'Субтитр {index}: проверьте порядок, пересечение и границы видео')
        text=cue['text']
        if not isinstance(text,str) or not text.strip() or len(text)>1000 or re.search(r'[\x00-\x08\x0b-\x1f\x7f]',text.replace('\r\n','\n')):raise ValueError('Нужен текст субтитра до 1000 символов без управляющих знаков')
        text=text.replace('\r\n','\n').strip()
        if '\n\n' in text:raise ValueError('Пустая строка внутри субтитра не поддерживается')
        size+=len(text.encode())
        if size>512000:raise ValueError('Текст субтитров превышает 500 КиБ')
        result.append({'id':ident,'start_us':start,'end_us':stop,'text':text});seen.add(ident);end=stop
    return result

def parse_time(value):
    match=re.fullmatch(r'(\d{2}):([0-5]\d):([0-5]\d)[,.](\d{3})',value)
    if not match:raise ValueError('Время должно иметь вид 00:00:01,250')
    h,m,s,ms=map(int,match.groups());return ((h*3600+m*60+s)*1000+ms)*1000

def parse_srt(text,duration):
    if not isinstance(text,str) or len(text.encode())>1048576:raise ValueError('SRT должен быть текстом до 1 МиБ')
    text=text.lstrip('\ufeff').replace('\r\n','\n').strip();result=[]
    for block in re.split(r'\n[ \t]*\n',text):
        lines=block.splitlines()
        if len(lines)<3 or not re.fullmatch(r'\d{1,6}',lines[0].strip()):raise ValueError('Нужен SRT: номер, строка времени и текст')
        times=lines[1].strip().split(' --> ')
        if len(times)!=2:raise ValueError('Некорректная строка времени SRT')
        result.append({'id':'srt-'+str(len(result)+1),'start_us':parse_time(times[0]),'end_us':parse_time(times[1]),'text':'\n'.join(lines[2:])})
    return cues(result,duration)

def timecode(us,separator):
    ms=us//1000;hours,ms=divmod(ms,3600000);minutes,ms=divmod(ms,60000);seconds,ms=divmod(ms,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}{separator}{ms:03}'

def render(track,format,plain=False):
    if format not in ('srt','vtt'):raise ValueError('Формат должен быть SRT или WebVTT')
    # Track text is plain text, never interpreted as provider-supplied markup.
    sep='.' if format=='vtt' else ','
    return ('WEBVTT\n\n' if format=='vtt' else '')+'\n\n'.join(f'{i}\n{timecode(cue["start_us"],sep)} --> {timecode(cue["end_us"],sep)}\n'+(cue['text'] if plain else html.escape(cue['text'],quote=False)) for i,cue in enumerate(track['cues'],1))+'\n'

def get(store,ident,lang):
    language(lang)
    with store.db() as c:
        media=_media(c,ident);row=c.execute('SELECT snapshot FROM content_captions WHERE import_id=? AND language=?',(ident,lang)).fetchone()
        track=json.loads(row['snapshot']) if row else None
        if track and track['asset_sha256']!=media['sha256']:raise ValueError('Файл субтитров привязан к другой контрольной сумме')
        return {'track':track,'editable_srt':render(track,'srt',plain=True) if track else '', 'import_id':ident,'language':lang,'asset_sha256':media['sha256'],'duration_us':media['duration_us']}

def save(store,args):
    ident=args.get('import_id');lang=language(args.get('language'));expected=integer(args.get('expected_revision'),'Версия субтитров',0)
    key=args.get('request_key')
    if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('Нужен ключ сохранения субтитров')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');media=_media(c,ident);normalized=cues(args.get('cues'),media['duration_us'])
        digest=hashlib.sha256(dump({'import_id':ident,'language':lang,'expected_revision':expected,'cues':normalized}).encode()).hexdigest()
        previous=c.execute('SELECT * FROM request_keys WHERE key=?',(key,)).fetchone()
        if previous:
            if previous['command']!='caption_save' or previous['payload_hash']!=digest:raise ValueError('request_conflict: ключ использован с другими данными')
            return json.loads(previous['result'])
        existing=c.execute('SELECT snapshot FROM content_captions WHERE import_id=? AND language=?',(ident,lang)).fetchone()
        row=json.loads(existing['snapshot']) if existing else None
        if row and row['asset_sha256']!=media['sha256']:raise ValueError('Субтитры привязаны к другой контрольной сумме')
        if (row['revision'] if row else 0)!=expected:raise ValueError('revision_conflict: субтитры изменены. Введённый текст сохранён в форме.')
        if row and row['archived']:raise ValueError('Сначала восстановите субтитры из архива')
        if row and row['cues']==normalized:result=row
        else:
            result={'id':row['id'] if row else uuid.uuid4().hex,'import_id':ident,'language':lang,'revision':expected+1,'archived':False,
                'asset_sha256':media['sha256'],'duration_us':media['duration_us'],'cues':normalized,'timing_origin':'editor_supplied','alignment_verified':False,'updated_at':now()}
            encoded=dump(result)
            c.execute('INSERT INTO content_captions VALUES (?,?,?,?,?) ON CONFLICT(import_id,language) DO UPDATE SET revision=excluded.revision,snapshot=excluded.snapshot',(result['id'],ident,lang,result['revision'],encoded))
            c.execute('INSERT INTO content_caption_revisions VALUES (?,?,?)',(result['id'],result['revision'],encoded))
        c.execute('INSERT INTO request_keys VALUES (?,?,?,?)',(key,'caption_save',digest,dump(result)))
        return result

def archive(store,args):
    language(args.get('language'));expected=integer(args.get('expected_revision'),'Версия субтитров',1);value=args.get('archived')
    if type(value) is not bool:raise ValueError('Укажите состояние архива')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT snapshot FROM content_captions WHERE import_id=? AND language=?',(args.get('import_id'),args['language'])).fetchone()
        if not row:raise ValueError('Субтитры не найдены')
        row=json.loads(row['snapshot'])
        if row['revision']!=expected:raise ValueError('revision_conflict: субтитры изменились')
        if row['archived']==value:return row
        row={**row,'archived':value,'revision':expected+1,'updated_at':now()};encoded=dump(row)
        c.execute('UPDATE content_captions SET revision=?,snapshot=? WHERE id=?',(row['revision'],encoded,row['id']))
        c.execute('INSERT INTO content_caption_revisions VALUES (?,?,?)',(row['id'],row['revision'],encoded));return row

def exact(store,args):
    result=get(store,args.get('import_id'),args.get('language'));track=result['track']
    if not track or track['archived']:raise ValueError('Сохраните активные субтитры')
    if track['revision']!=integer(args.get('revision'),'Версия субтитров',1):raise ValueError('revision_conflict: выбрана другая версия субтитров')
    return track

def dispatch(store,cmd,args):
    allowed={'content_caption_get':{'import_id','language'},'content_caption_save':{'import_id','language','expected_revision','cues','request_key'},'content_caption_archive':{'import_id','language','expected_revision','archived'},'content_caption_parse':{'import_id','text'},'content_caption_export':{'import_id','language','revision','format'},'content_caption_preview':{'import_id','language','revision'}}
    if not isinstance(args,dict) or cmd not in allowed or set(args)-allowed[cmd]:raise ValueError('Некорректная команда субтитров')
    if cmd=='content_caption_get':return get(store,args.get('import_id'),args.get('language'))
    if cmd=='content_caption_save':return save(store,args)
    if cmd=='content_caption_archive':return archive(store,args)
    if cmd=='content_caption_parse':
        with store.db() as c:media=_media(c,args.get('import_id'))
        return {'cues':parse_srt(args.get('text'),media['duration_us'])}
    track=exact(store,args)
    if cmd=='content_caption_preview':
        from .video_preview import open_preview
        return {'url':open_preview(store,args['import_id'],track=track)}
    data=render(track,args.get('format')).encode()
    return {'filename':f'captions-{track["id"]}-v{track["revision"]}.{args["format"]}','mime':'text/vtt' if args['format']=='vtt' else 'application/x-subrip','base64':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest()}
