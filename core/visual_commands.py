"""Local text-to-annotation planning. OCR is data, never executable instructions.

Plans contain bounded marker objects and no writes to the user's annotation state.
The editor applies a chosen batch as one undo step, then uses visual_save validation.
"""
import hashlib
import json
import math
import re
import threading
import uuid
from difflib import SequenceMatcher

OCR_LOCK = threading.Lock()
COLORS = {r'красн(?:ый|ым|ого|ая|ую)':'#e5484d',r'син(?:ий|им|его|яя|юю)':'#1971c2',r'зелен(?:ый|ым|ого|ая|ую)':'#2f9e44',r'желт(?:ый|ым|ого|ая|ую)':'#ffd43b',r'черн(?:ый|ым|ого|ая|ую)':'#000000',r'оранжев(?:ый|ым|ого|ая|ую)':'#f08c00'}


def norm(text):
    return ' '.join(re.findall(r'\w+', text.casefold().replace('ё','е'), re.UNICODE))


def union(boxes):
    x=min(b[0] for b in boxes);y=min(b[1] for b in boxes)
    return [x,y,max(b[0]+b[2] for b in boxes)-x,max(b[1]+b[3] for b in boxes)-y]


def index_result(raw, source):
    width,height=source['width'],source['height']
    def pixels(box):
        if not isinstance(box,(list,tuple)) or len(box)!=4 or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in box):
            return None
        x,y,w,h=box
        left=max(0,min(width,x*width));top=max(0,min(height,(1-y-h)*height))
        right=max(left,min(width,(x+w)*width));bottom=max(top,min(height,(1-y)*height))
        return [round(left,2),round(top,2),round(right-left,2),round(bottom-top,2)] if right>left and bottom>top else None
    lines=[]
    for n,line in enumerate(raw.get('lines',[])):
        box=pixels(line.get('box'))
        if not box:continue
        words=[]
        for j,word in enumerate(line.get('words',[])):
            wb=pixels(word.get('box'))
            if wb:words.append({'id':f'l{n}w{j}','text':str(word['text']),'box':wb,'confidence':word.get('confidence',0)})
        lines.append({'id':f'l{n}','text':str(line['text']),'box':box,'words':words,'confidence':line.get('confidence',0)})
    return {'schema':2,'source_sha256':source['sha256'],'width':width,'height':height,'engine':raw.get('engine',''),
            'status':raw.get('status','done'),'error':raw.get('error',''),'lines':lines}


def cache_result(src, data, raw, size):
    """Capture already paid the OCR cost; retain geometry outside the chat log."""
    from visual_assets import save
    folder=src.with_suffix('.разметка');folder.mkdir(exist_ok=True)
    source={'sha256':hashlib.sha256(data).hexdigest(),'width':size[0],'height':size[1]}
    save(folder/'ocr.json',index_result(raw,source))


def get_index(src, folder, source):
    from visual_assets import save
    from ocr import recognize_result
    with OCR_LOCK:
        path=folder/'ocr.json'
        if path.exists():
            try:
                cached=json.loads(path.read_text())
                if cached.get('schema')==2 and cached.get('source_sha256')==source['sha256'] and cached.get('status')!='error':return cached
            except (ValueError,OSError):pass
        data=src.read_bytes()
        if hashlib.sha256(data).hexdigest()!=source['sha256']:raise ValueError('Оригинал изменён. Откройте снимок заново.')
        result=index_result(recognize_result(data),source)
        save(path,result)
        return result


def parse(text):
    if not isinstance(text,str) or not text.strip() or len(text)>2000:raise ValueError('Введите команду до 2000 знаков')
    text=text.strip().rstrip('.!');quoted=re.findall(r'[«“"]([^»”"]+)[»”"]',text)
    plain=re.sub(r'[«“"][^»”"]+[»”"]',' ',text).casefold().replace('ё','е')
    plain=re.sub(r'^лира[,\s]+','',plain).strip()
    color=next((c for stem,c in COLORS.items() if re.search(r'\b'+stem+r'\b',plain)), '#e5484d')
    if re.match(r'^(скрой|скрыть|закрась)\s+(личное|личные данные|персональные данные)\s*$',plain):return {'op':'private','color':'#000000'}
    verbs=[(r'(подчеркни|подчеркнуть)','underline'),(r'(обведи|обвести|выдели|выделить)','frame'),
           (r'(скрой|скрыть|закрась|закрасить)','cover'),(r'(подпиши|подписать)','label'),
           (r'(пронумеруй|пронумеровать)','number'),(r'(?:(?:нарисуй|добавь)\s+)?стрелк\w*','arrow')]
    found=next(((re.match('^'+pattern+r'\b',plain),op) for pattern,op in verbs if re.match('^'+pattern+r'\b',plain)),None)
    if not found:raise ValueError('Пример: обведи «Сохранить» красным; подчеркни «Итого»; стрелка к «Далее».')
    match,op=found
    if op=='frame' and re.search(r'\b(круг\w*|овал\w*)\b',plain):op='ellipse'
    tail=plain[match.end():]
    # Modifiers are removed only outside quoted targets.
    tail=re.sub(r'\b(?:'+ '|'.join(COLORS)+r')\b',' ',tail)
    tail=re.sub(r'\b(цветом|кругом|кружком|овалом|рамкой|кнопку|кнопка|текст|надпись|слово|фразу|все|всех|к|на|вокруг)\b',' ',tail)
    if op=='label':
        if len(quoted)!=2:raise ValueError('Подпись: подпиши «Сохранить» «Нажмите здесь».')
        target,label=quoted
    else:target=quoted[0] if quoted else tail.strip();label=''
    if not norm(target):raise ValueError('Укажите текст на снимке, например «Сохранить».')
    return {'op':op,'target':target,'label':label,'color':color}


def matches(index, target):
    wanted=norm(target);count=len(wanted.split());exact=[];fuzzy=[];seen=set()
    lines=[{**l,'id':l.get('id',f'line{n}'),'box':l.get('box') or union([w['box'] for w in l['words']])} for n,l in enumerate(index['lines'])]
    original_lines=list(lines)
    # Vision can return neighbouring words of one visual row as separate observations.
    # Join only real boxes on the same baseline, never invent word coordinates.
    ordered=sorted(lines,key=lambda l:(l['box'][1],l['box'][0]))
    for first in ordered:
        group=[first];x,y,w,h=first['box'];right=x+w
        for other in sorted((l for l in original_lines if l is not first and l['box'][0]>=right),key=lambda l:l['box'][0]):
            ox,oy,ow,oh=other['box'];overlap=min(y+h,oy+oh)-max(y,oy)
            if overlap>=min(h,oh)*.6 and ox-right<=max(h,oh)*2.5:
                group.append(other);right=ox+ow
        if len(group)>1:
            joined=[word for l in group for word in (l['words'] or [l])]
            lines.append({'id':first['id']+'joined','text':' '.join(l['text'] for l in group),'box':union([l['box'] for l in group]),'words':joined})
    for line in lines:
        words=line['words']
        if not words:
            spans=[([line],line['text'])]
        else:
            spans=[]
            for start in range(len(words)):
                for end in range(start+1,min(len(words),start+count+2)+1):
                    span=words[start:end];value=' '.join(w['text'] for w in span)
                    if abs(len(norm(value).split())-count)<=1:spans.append((span,value))
        for span,value in spans:
            normalized=norm(value)
            if not normalized:continue
            score=1 if normalized==wanted else SequenceMatcher(None,wanted,normalized).ratio()
            if score<.72:continue
            box=union([w['box'] for w in span]);key=tuple(box)
            if key in seen:continue
            seen.add(key)
            candidate={'id':span[0]['id']+'-'+span[-1]['id'],'text':value,'context':line['text'],'box':box,'exact':score==1,'score':round(score,3)}
            (exact if score==1 else fuzzy).append(candidate)
    return exact if exact else sorted(fuzzy,key=lambda x:-x['score'])[:12]


def luhn(text):
    digits=[int(x) for x in re.sub(r'\D','',text)]
    if not 13<=len(digits)<=19:return False
    return sum((n*2-9 if n*2>9 else n*2) if i%2 else n for i,n in enumerate(reversed(digits)))%10==0


def private_matches(index):
    patterns=[('E-mail',r'[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}'),
              ('Телефон',r'(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[)\s.-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)'),
              ('Номер карты',r'(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)'),
              ('IP-адрес',r'(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)'),
              ('Ключ',r'\b(?:sk-|ghp_|AKIA)[A-Za-z0-9_-]{12,}\b|\b[A-Fa-f0-9]{32,}\b|(?=[A-Za-z0-9+/=_-]{32,}\b)(?=[A-Za-z0-9+/=_-]*\d)[A-Za-z0-9+/=_-]{32,}\b'),
              ('Пароль',r'(?i)(?:пароль|password)\s*[:=]\s*\S+')]
    output=[];seen=set()
    for line in index['lines']:
        words=line['words'] or [line];text='';offsets=[]
        for word in words:
            if text:text+=' '
            start=len(text);text+=word['text'];offsets.append((start,len(text),word))
        for kind,pattern in patterns:
            for match in re.finditer(pattern,text):
                if kind=='Номер карты' and not luhn(match.group()):continue
                if kind=='IP-адрес' and any(int(x)>255 for x in match.group().split('.')):continue
                span=[w for a,b,w in offsets if a<match.end() and b>match.start()]
                box=union([w['box'] for w in span]);key=tuple(box)
                if key in seen:continue
                seen.add(key);output.append({'id':span[0]['id']+'-'+span[-1]['id'],'text':kind,'context':kind,'box':box,'exact':True,'score':1})
    return output


def visible(candidate, doc):
    x,y,w,h=candidate['box'];crop=doc.get('crop')
    if crop and not (x>=crop['x'] and y>=crop['y'] and x+w<=crop['x']+crop['width'] and y+h<=crop['y']+crop['height']):return False
    from visual_compose import _corners
    for marker in doc['state']['markers']:
        if marker['typeName']!='CoverMarker':continue
        points=_corners(marker,2);left=min(p[0] for p in points);top=min(p[1] for p in points)
        right=max(p[0] for p in points);bottom=max(p[1] for p in points)
        if x<right and x+w>left and y<bottom and y+h>top:return False
    return True


def marker_for(candidate, spec, source, number=1):
    x,y,w,h=candidate['box'];scale=max(1,source['width']/1600);pad=4*scale
    x=max(0,x-pad);y=max(0,y-pad);w=min(source['width']-x,w+2*pad);h=min(source['height']-y,h+2*pad)
    color=spec['color'];op=spec['op'];base={'opacity':1,'notes':'dewiar:id='+uuid.uuid4().hex}
    if op in ('frame','ellipse','cover','private'):
        base.update(typeName={'frame':'FrameMarker','ellipse':'EllipseFrameMarker','cover':'CoverMarker','private':'CoverMarker'}[op],left=x,top=y,width=w,height=h,rotationAngle=0,strokeColor=color,strokeWidth=3*scale)
        if op in ('cover','private'):base.update(fillColor='#000000',strokeColor='#000000',strokeWidth=0)
    elif op in ('underline','arrow'):
        base.update(typeName='LineMarker' if op=='underline' else 'ArrowMarker',strokeColor=color,strokeWidth=3*scale,
                    x1=x if op=='underline' else max(0,x-60*scale),y1=min(source['height']-1,y+h) if op=='underline' else max(0,y-40*scale),
                    x2=x+w if op=='underline' else x,y2=min(source['height']-1,y+h) if op=='underline' else y)
        if op=='arrow':base['arrowType']='end'
    else:
        label=spec.get('label','') if op=='label' else (chr(0x2460+number-1) if number<=20 else str(number))
        size=24*scale;label_width=min(source['width'],max(70*scale,len(label)*size*.7))
        base.update(typeName='TextMarker',text=label,color=color,fontFamily='Helvetica, Arial, sans-serif',fontSize={'value':size,'units':'px','step':1},
                    left=min(x,source['width']-label_width),top=max(0,y-35*scale),width=label_width,height=32*scale,rotationAngle=0)
        if op=='number':base['notes']='dewiar:number='+str(number)
    return base


def plan(src, folder, doc, text):
    spec=parse(text);index=get_index(src,folder,doc['source'])
    if index['status']=='error':raise ValueError('Не удалось распознать снимок: '+index['error'])
    candidates=private_matches(index) if spec['op']=='private' else matches(index,spec['target'])
    candidates=[c for c in candidates if visible(c,doc)]
    if len(candidates)>100:raise ValueError('Найдено больше 100 областей. Уточните текст команды.')
    numbers=[int(m.group(1)) for row in doc['state']['markers'] if (m:=re.fullmatch(r'dewiar:number=(\d+)',row.get('notes','')))]
    for n,candidate in enumerate(candidates,max(numbers,default=0)+1):candidate['marker']=marker_for(candidate,spec,doc['source'],n)
    from visual_assets import validate
    validate({**doc['state'],'markers':[c['marker'] for c in candidates]},doc['source'],doc['crop'])
    return {'revision':doc['revision'],'op':spec['op'],'engine':index['engine'],'candidates':candidates,
            'message':('Ничего подходящего не найдено. Уточните текст или отметьте область вручную.' if not candidates else ''),
            'review':spec['op']=='private' or len(candidates)!=1 or not candidates[0]['exact']}
