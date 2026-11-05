"""Conservative Russian transcript editing. Never edits audio or video.

Edits and review candidates refer to the original joined transcript, retaining audio provenance.
Ambiguous content words and semantic restarts stay in the clean text until audio review.
"""
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

VERSION = 'ru-editor-2'
WORD = r'[A-Za-zА-Яа-яЁё0-9]+(?:[-‑][A-Za-zА-Яа-яЁё0-9]+)*'
PHRASES = [
    'сразу хочу сказать','как я говорил','как я говорю','да то есть','вот то есть',
    'у нас сейчас','таким образом','соответственно','вообщем то','в общем-то','в общем то',
    'так сказать','это самое','в целом','как бы','то есть','допустим','короче',
    'значит','кстати','скажем','у нас','вот','там','ну','да',
]
PHRASE_RX = re.compile(r'(?<![\w-])(?:' + '|'.join(re.escape(p).replace(r'\ ',r'\s+') for p in sorted(PHRASES,key=len,reverse=True)) + r')(?![\w-])', re.I)
VOCAL_RX = re.compile(r'(?<![\w-])(?:э(?:[-–—]?э)*|эм|м{2,}|и{3,})(?![\w-])', re.I)
ABBREVIATIONS = {'г','гг','д','др','т','е','п','им','рис','стр','см','мм','м','кг','рт','ст','кв','ул','пр','проф','руб','коп','млн','млрд','тыс','квт','ква'}
EMPHASIS = {'да','нет','очень','никогда','всегда','совсем','еле','чуть','долго','много','мало','снова'}


def _sentence_spans(text):
    start = 0
    for match in re.finditer(r'[.!?…][»”\"]?(?:\s+|$)', text):
        if text[match.start()] == '.':
            word = re.search(r'([\w-]+)$',text[:match.start()])
            if word and (word[1].lower() in ABBREVIATIONS or len(word[1]) == 1):
                continue
            after = text[match.end():].lstrip('«“"(')
            if after and not (after[0].isupper() or after[0].isdigit()):
                continue
        if text[start:match.end()].strip():
            yield start, match.end()
            start = match.end()
    if text[start:].strip():
        yield start, len(text)


def _normal(text):
    return ' '.join(re.findall(WORD,text.lower().replace('ё','е')))


def _paragraphs(text):
    parts, paragraph = [], ''
    for a,b in _sentence_spans(text):
        sentence = text[a:b].strip()
        paragraph = (paragraph+' '+sentence).strip()
        if len(paragraph) >= 650:
            parts.append(paragraph);paragraph=''
    if paragraph:
        parts.append(paragraph)
    return '\n\n'.join(parts)


def _spacing(text):
    # Mark removed speech before normalizing punctuation, so paired parenthetical
    # commas disappear together instead of becoming a spurious phrase boundary.
    text=re.sub(r',\s*(?:\x00[\s,]*)+,',' ',text)
    text=text.replace('\x00',' ')
    text = re.sub(r'\s+',' ',text).strip()
    text = re.sub(r'\s+([,.;:!?])',r'\1',text)
    text = re.sub(r'(?:,\s*){2,}',', ',text)
    text = re.sub(r',\s*([.!?;:])',r'\1',text)
    text = re.sub(r'([.!?…—–])\s*[,;:]+\s*',r'\1 ',text)
    text = re.sub(r'(^|(?<=[.!?])\s+)[,;:]\s*',r'\1',text)
    text = re.sub(r'\s+',' ',text).strip(' ,;:')
    if text:
        text=text[0].upper()+text[1:]
    def capitalize(match):
        previous=re.search(r'([\w-]+)$',text[:match.start()])
        if previous and (previous[1].lower() in ABBREVIATIONS or len(previous[1])==1):
            return match[0]
        return match[1]+match[2].upper()
    text=re.sub(r'([.!?]\s+)([а-яё])',capitalize,text)
    return text


def edit(segments, title='Расшифровка'):
    chunks, locations, offset = [], [], 0
    for a,b,raw in segments:
        text = re.sub(r'\s+',' ',str(raw)).strip()
        if not text:
            continue
        if chunks:
            offset += 1
        locations.append((offset,offset+len(text),float(a),float(b)))
        chunks.append(text);offset+=len(text)
    original = ' '.join(chunks)
    removals, review = [], []
    quotes = [m.span() for m in re.finditer(r'«[^»]*»|“[^”]*”|"[^"\n]*"',original)]

    def item(a,b,reason,kind,**extra):
        touched=[loc for loc in locations if loc[0]<b and loc[1]>a]
        return dict(kind=kind,text=original[a:b],reason=reason,offset_start=a,offset_end=b,
                    audio_start=touched[0][2] if touched else None,audio_end=touched[-1][3] if touched else None,
                    context=original[max(0,a-90):min(len(original),b+110)],**extra)
    def covered(a,b):
        return any(a < e['offset_end'] and b > e['offset_start'] for e in removals)
    def drop(a,b,reason,kind='filler'):
        if not covered(a,b):
            removals.append(item(a,b,reason,kind))
    def check(a,b,reason,kind='ambiguous_filler',**extra):
        if not covered(a,b):
            review.append(item(a,b,reason,kind,**extra))
    def quoted(a,b):
        return any(q1<=a and b<=q2 for q1,q2 in quotes)
    def isolated(a,b):
        left=original[:a].rstrip();right=original[b:].lstrip()
        return (not left or left[-1] in ',.!?;:—') and (not right or right[0] in ',.!?;:—')

    sentences=list(_sentence_spans(original))
    for i in range(len(sentences)-1):
        a,b=sentences[i];c,d=sentences[i+1]
        first,second=original[a:b].strip(),original[c:d].strip()
        n1,n2=_normal(first),_normal(second)
        if min(len(n1.split()),len(n2.split())) < 3:
            continue
        if n1==n2 and n1.split()[0] not in EMPHASIS:
            drop(a,c,'Точный соседний дубль фразы; сохранено последнее произнесение','duplicate_phrase')
        elif len(n1.split())>=4 and len(n2.split())>=4 and SequenceMatcher(None,n1,n2,autojunk=False).ratio()>=.72:
            check(a,d,'Похожие фразы могут быть оговоркой или разными утверждениями. Правильный вариант определяется по аудио.',
                  'semantic_repeat',suggestion='Сверить оба варианта с аудио; обычно оставить последнее корректное произнесение',variants=[first,second])

    for match in VOCAL_RX.finditer(original):
        a,b=match.span();value=match[0]
        if covered(a,b):
            continue
        if value.lower()=='мм' and (re.search(r'\d\s*$',original[:a])
                or re.search(r'\b(?:в|размер\w*|измер\w*|единиц\w*)\s*$',original[:a],re.I)
                or re.match(r'\s*(?:рт\.|вод\.|/)',original[b:],re.I)):
            continue  # millimetres, including decimal quantities: never remove a measurement unit
        if quoted(a,b) or value.isupper() or re.search(r'(?:буква|символ|маркировка)\s*$',original[:a],re.I):
            check(a,b,'Возможны обозначение, цитата или техническое сокращение')
        else:
            drop(a,b,'Неречевой звук/заполнитель паузы','vocal')

    for match in PHRASE_RX.finditer(original):
        a,b=match.span();value=' '.join(match[0].lower().split())
        if covered(a,b):
            continue
        left,right=original[:a].rstrip(),original[b:].lstrip()
        at_start=not left or left[-1] in '.!?…'
        if quoted(a,b):
            check(a,b,'Слово или выражение находится в цитате; не удаляется автоматически')
        elif value=='да то есть' and left.endswith('?'):
            tail=re.search(r'то\s+есть',match[0],re.I)
            drop(a+tail.start(),b,'Пояснительная вставка; осмысленный ответ «да» сохранён')
        elif value=='да':
            standalone = at_start and (not right or right[0] in '.!?…')
            answer = bool(re.search(r'\?\s*$',left)) or bool(left and left[-1] in ':—')
            if standalone or answer:
                continue
            if left.endswith(',') and (not right or right[0] in '.!'):
                drop(a,b,'Частица «да» в конце фразы')
            elif at_start and right.startswith(','):
                check(a,b,'Начальное «да» может быть осмысленным утверждением, а не частицей')
            elif left.endswith(',') and right.startswith('?'):
                check(a,b,'Конечное «да?» может быть вопросом, требующим подтверждения')
        elif value=='короче':
            if (at_start or left.endswith(',')) and right.startswith(','):
                drop(a,b,'Вводное «короче» в начале предложения или после запятой')
            elif at_start or left.endswith(','):
                check(a,b,'«Короче» может означать меньшую длину')
        elif value=='вот' and re.match(r'(?:где|почему|как|что)\b',right,re.I):
            check(a,b,'«Вот» связывает смысловую конструкцию, а не только заполняет паузу')
        elif value=='как бы' and re.match(r'(?:не|ни|вы|ты|мы|нам|мне)\b',right,re.I):
            check(a,b,'Возможна условная конструкция или опасение, а не слово-паразит')
        elif value in ('там','у нас','у нас сейчас','значит','соответственно','допустим','скажем','таким образом','в целом','это самое','то есть'):
            if isolated(a,b) and value!='там' and not (value in ('допустим','скажем') and at_start):
                drop(a,b,'Обособленное вводное выражение из списка очистки')
            else:
                check(a,b,'Возможен содержательный смысл: место, принадлежность, условие, пояснение или связь терминов')
        else:
            drop(a,b,'Слово или выражение-паразит из заданного списка')

    # Exact word stutters; meaningful emphasis and repeated affirmations stay for review.
    repeat=re.compile(r'(?<!\w)('+WORD+r')(?:(?:\s*,\s*|\s+)\1)+(?!\w)',re.I)
    for match in repeat.finditer(original):
        a,b=match.span();first=match[1]
        if covered(a,b):
            continue
        if (first.lower() in EMPHASIS or quoted(a,b) or any(c.isdigit() for c in first)
                or (first.isupper() and len(first)>1) or (len(first)==1 and first.isascii())):
            check(a,b,'Повтор может быть намеренным усилением или подтверждением','word_repeat')
        else:
            drop(a,match.start()+match[0].lower().rfind(first.lower()),'Повтор одного слова; сохранено последнее слово','word_repeat')

    # Exact adjacent multiword restarts without sentence punctuation.
    phrase_repeat=re.compile(r'(?<!\w)((?:'+WORD+r'\s+){2,6}'+WORD+r')[,\s]+\1(?!\w)',re.I)
    for match in phrase_repeat.finditer(original):
        a,b=match.span();phrase=match[1]
        if covered(a,b):
            continue
        if phrase.split()[0].lower() in EMPHASIS or quoted(a,b):
            check(a,b,'Повтор фразы может быть намеренным усилением','phrase_repeat')
        else:
            last=a+match[0].lower().rfind(phrase.lower())
            drop(a,last,'Точный соседний повтор фразы; сохранён последний вариант','duplicate_phrase')

    for match in re.finditer(r'[\u0600-\u06ff\u0e00-\u0e7f\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]+',original):
        check(*match.span(),'Нетипичный для русской записи фрагмент: возможны артефакт распознавания или настоящая иностранная речь.','recognition_artifact')

    cleaned=original
    for removal in sorted(removals,key=lambda e:e['offset_start'],reverse=True):
        cleaned=cleaned[:removal['offset_start']]+'\x00'+cleaned[removal['offset_end']:]
    cleaned=_spacing(cleaned)
    if not cleaned and original:
        review.append(item(0,len(original),'После очистки не осталось содержательного текста. Нужна сверка записи.','empty_clean'))
    review=[r for r in review if not any(r['offset_start']>=e['offset_start'] and r['offset_end']<=e['offset_end'] for e in removals)]
    heading=re.sub(r'\s+',' ',str(title or 'Расшифровка')).strip()
    markdown='# '+heading+'\n\n'+_paragraphs(cleaned)+'\n'
    return dict(version=VERSION,raw_sha256=hashlib.sha256(original.encode()).hexdigest(),
                markdown=markdown,removals=sorted(removals,key=lambda e:e['offset_start']),review=review,
                removed_count=len(removals),review_count=len(review),audio_review_completed=False)


def clean_text(text):
    """Apply the transcript cleanup rules to plain text, without audio timing or a document heading."""
    result = edit([(0, 0, str(text or ""))], title="Текст")
    return dict(text=result["markdown"].split("\n\n", 1)[1].rstrip() if "\n\n" in result["markdown"] else "",
                removed_count=result["removed_count"], review_count=result["review_count"])


def _review_link(source, seconds):
    if str(source).startswith(('http://','https://')):
        u=urlsplit(source)
        if (u.hostname or '').endswith(('youtube.com','youtu.be')) and seconds is not None:
            query=[(k,v) for k,v in parse_qsl(u.query) if k not in ('t','start')]+[('t',str(max(0,int(seconds))))]
            return urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),''))
        return source
    try:
        return Path(source).expanduser().resolve().as_uri()
    except ValueError:
        return ''


def ensure_clean(directory, title, source, notify=lambda message:None):
    directory=Path(directory)
    segments=json.loads((directory/'segments.json').read_text(encoding='utf-8'))
    raw=' '.join(re.sub(r'\s+',' ',str(row[2])).strip() for row in segments if str(row[2]).strip())
    raw_hash=hashlib.sha256(raw.encode()).hexdigest()
    try:
        old=json.loads((directory/'cleanup.json').read_text(encoding='utf-8'))
        if (old.get('version')==VERSION and old.get('raw_sha256')==raw_hash
                and (directory/'transcript.clean.md').is_file() and (directory/'transcript.review.md').is_file()):
            return {k:old[k] for k in ('version','removed_count','review_count','audio_review_completed')}
    except (OSError,ValueError,KeyError):
        pass
    notify('Этап 2/3 — готовлю связный Markdown и очищаю текст')
    result=edit(segments,title)
    notify('Этап 3/3 — сохраняю чистовой текст и места для сверки с аудио')
    markdown=result.pop('markdown')
    review=['# Сверка по аудио','', 'Неоднозначные места сохранены в чистовом тексте. Аудио ещё не проверено.','']
    for item in result['review']:
        context=item['context'].replace('\n',' ')
        link=_review_link(source,item.get('audio_start'))
        review += ['- **'+item['text']+'** — '+item['reason'], '  Контекст: '+context,
                   '  '+item.get('suggestion','Проверить по аудио перед изменением текста.')]
        if link:
            review.append('  [Сверить с аудио]('+link+')')
        review.append('')
    if not result['review']:
        review.append('Мест для ручной сверки по правилам очистки не обнаружено.')
    for name,content in [('transcript.clean.md',markdown),('transcript.review.md','\n'.join(review)+'\n'),('cleanup.json',json.dumps(result,ensure_ascii=False,indent=2)+'\n')]:
        temp=directory/(name+'.tmp');temp.write_text(content,encoding='utf-8');temp.replace(directory/name)
    return {k:result[k] for k in ('version','removed_count','review_count','audio_review_completed')}
