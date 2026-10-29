"""Local birthday reminder dialogue. No model calls; no reminder before explicit choices."""
import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from when import annual_date, next_time, describe_repeat, human, _norm, _clock

BIRTHDAY = re.compile(r'\b(?:день\s+рождения|дн(?:е|ём|ем|я)\s+рождения|др)\b',re.I)


def invalid_clock(text):
    return bool(re.search(r'(?:\b(?:в|на|к)\s+|^)\d{1,2}:\d{2}',text) and not _clock(text if re.search(r'\b(?:в|на|к)\s+',text) else 'в '+text))


def offsets(text):
    t = _norm(text)
    found = []
    if re.search(r'\b(?:в\s+(?:сам\s+)?день|в этот день|день в день|в дату)\b',t): found.append(0)
    if re.search(r'\bза\s+(?:один\s+)?день\b|\bнакануне\b',t): found.append(1)
    if re.search(r'\bза\s+(?:одну\s+)?неделю\b',t): found.append(7)
    found += [int(n) for n in re.findall(r'\bза\s+(\d+)\s+(?:дн(?:я|ей)|день|сут(?:ки|ок))\b',t)]
    found += [7*int(n) for n in re.findall(r'\bза\s+(\d+)\s+недел\w*',t)]
    return sorted(set(found)) if found else None


def candidate(text):
    t = _norm(text)
    marker = BIRTHDAY.search(t)
    if not marker or re.search(r'\b(?:как|что|почему|когда|сколько|поздрав\w*|купить|подар\w*|отмени|удали|список)\b|\bне\s+(?:напоминай|надо|нужно)',t):
        return None
    date = annual_date(t)
    # A personal statement or reminder request, not a generic discussion about birthdays.
    if not date and not re.match(r'^(?:запомни[, :]*\s*)?у\s+\S+',t): return None
    name = BIRTHDAY.sub(' ',t)
    if not date:
        name = re.sub(r'\b\d{1,2}\s+(?:январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*|\b\d{1,2}[./]\d{1,2}(?:[./]\d{4})?',' ',name)
    if date:
        a,b=date['span']; name = BIRTHDAY.sub(' ',t[:a]+' '+t[b:])
    clock = _clock(t)
    if clock:
        name = re.sub(r'\bв\s+\d{1,2}(?::\d{2})?(?:\s+(?:утра|дня|вечера|ночи))?\b',' ',name)
    name = re.sub(r'\b(?:напомни(?:те)?|напоминай(?:те)?|запомни|пожалуйста|ежегодно|каждый\s+год|раз\s+в\s+год|у|о|об|про|мне|это|будет)\b',' ',name)
    name = re.sub(r'\bза\s+(?:\d+\s+(?:дн\w*|недел\w*)|день|неделю)\b|\bв\s+(?:сам\s+)?день\b',' ',name)
    name = re.sub(r'\b(?:1\s+раз|разово)\b',' ',name)
    name = re.sub(r'\bи\s*$',' ',name)
    name = ' '.join(name.strip(' .,:—-').split())
    if not name or len(name)>100 or len(name.split())>6: return None
    return dict(date or {},name=name[:1].upper()+name[1:],annual=None,offsets=offsets(t),
                time=f'{clock[0]:02d}:{clock[1]:02d}' if clock else '09:00',leap_day=None)


class BirthdayDialog:
    def __init__(self,path,reminders):
        self.path=Path(path); self.reminders=reminders; self.lock=threading.RLock()
        try:self.pending=json.loads(self.path.read_text())
        except (OSError,ValueError):self.pending={}
        if not isinstance(self.pending,dict):self.pending={}

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.pending,ensure_ascii=False),encoding='utf-8'); tmp.chmod(0o600); tmp.replace(self.path)

    def handle(self,text,scope,now=None):
        with self.lock:
            return self._handle(text,str(scope),now or datetime.now())

    def _handle(self,text,scope,now):
        t=_norm(text); new=candidate(text)
        p=self.pending.get(scope)
        if p and (now.timestamp()-p.get('updated',0)>7*86400):
            self.pending.pop(scope,None); p=None; self.save()
        if new:
            p=new; p['updated']=now.timestamp(); p['invalid_time']=invalid_clock(t); self.pending[scope]=p; self.save()
            label=f"День рождения {p['name']}"+(f" — {p['day']:02d}.{p['month']:02d}" if p.get('month') else '')
            return {'text':label+'. Сделать напоминания ежегодными или один раз? Когда напоминать: в сам день, за день, за неделю или в несколько сроков? Если время не указано, напомню в 9:00. Для отмены скажите «не нужно».','items':[]}
        if not p:return None
        if invalid_clock(t):
            p['invalid_time']=True; self.save()
            return {'text':'Уточните время от 00:00 до 23:59. Например, в 9:00.','items':[]}
        if re.search(r'\bза\s+(?:\d+\s+)?месяц\w*',t):
            return {'text':'Укажите, за сколько дней напоминать, например за 30 дней.','items':[]}
        if re.fullmatch(r'(?:нет|не надо|не нужно|не напоминай|отмена|отмени|забудь)[ .!]*',t):
            self.pending.pop(scope,None); self.save()
            return {'text':'Хорошо, напоминания о дне рождения не создаю.','items':[]}
        annual = True if re.search(r'\b(?:ежегодно|каждый\s+год|раз\s+в\s+год)\b',t) else False if re.search(r'\b(?:разово|1\s+раз|только\s+в\s+этом\s+году|не\s+ежегодно)\b',t) else None
        if re.search(r'\bне\s+(?:ежегодно|каждый\s+год)\b',t):annual=False
        dates=annual_date(t); off=offsets(t); clock=_clock(t)
        if not dates and re.search(r'\b\d{1,2}\s+(?:январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*|\b\d{1,2}[./]\d{1,2}',t):
            return {'text':'Такой даты нет. Уточните день и месяц рождения.','items':[]}
        if not clock and re.fullmatch(r'\d{1,2}(?::\d{2})?(?:\s+(?:утра|дня|вечера|ночи))?[ .!]*',t):
            clock=_clock('в '+t)
        yes=bool(re.match(r'^(?:да|нужно|надо)(?:\W|$)',t) or re.fullmatch(r'(?:сделай|включи)[ .!]*',t))
        leap = 'feb28' if re.search(r'28\s+феврал|28[./]0?2',t) else 'mar1' if re.search(r'1\s+марта|0?1[./]0?3',t) else None
        if not (annual is not None or dates or off is not None or clock or yes or leap):
            # A new topic must not be interpreted as consent or trap the conversation.
            self.pending.pop(scope,None); self.save(); return None
        if annual is not None:p['annual']=annual
        elif yes and p.get('annual') is None:p['annual']=True
        if 'в этом году' in t:p['once_year']=now.year
        elif 'ближайш' in t:p.pop('once_year',None)
        if dates and not (p.get('month')==2 and p.get('day')==29 and leap):p.update(dates)
        if off is not None:
            if any(x>365 for x in off) or len(off)>12:return {'text':'Выберите до 12 сроков: в сам день или за 1–365 дней.','items':[]}
            p['offsets']=off
        if clock:
            p['time']=f'{clock[0]:02d}:{clock[1]:02d}'; p.pop('invalid_time',None)
        if not p.get('time'):p['time']='09:00'  # Includes unfinished dialogues saved by older versions.
        if leap:p['leap_day']=leap
        p['updated']=now.timestamp(); self.save()
        if not p.get('month'):question='Назовите дату дня рождения: например, 15 мая.'
        elif p.get('annual') is None:question='Напоминать ежегодно или только один раз?'
        elif p.get('offsets') is None:question='Когда напоминать: в сам день, за день, за неделю или в несколько сроков?'
        elif p.get('invalid_time'):question='Уточните время от 00:00 до 23:59. Например, в 9:00.'
        elif p['month']==2 and p['day']==29 and not p.get('leap_day'):question='В невисокосный год о 29 февраля напоминать 28 февраля или 1 марта?'
        else:question=None
        if question:return {'text':question,'items':[]}
        title='День рождения '+p['name']; items=[]; details=[]
        if not p['annual'] and p.get('once_year'):
            for offset in p['offsets']:
                check=next_time({'kind':'yearly','month':p['month'],'day':p['day'],'time':p['time'],'offset_days':offset,'leap_day':p.get('leap_day') or 'feb28'},now)
                if check.year>p['once_year']:
                    return {'text':'В этом году выбранный срок уже прошёл. Для следующей даты скажите «один раз в ближайший срок» или измените сроки.','items':[]}
        for offset in p['offsets']:
            rep={'kind':'yearly','month':p['month'],'day':p['day'],'time':p['time'],'offset_days':offset,'leap_day':p.get('leap_day') or 'feb28'}
            at=next_time(rep,now)
            label=title+(f' — через {offset} дн.' if offset else '')
            # Idempotent after a crash/repeated setup; exact label+rule identifies our reminder.
            with self.reminders._lock:
                existing=next((dict(x) for x in self.reminders.items if x.get('text')==label and x.get('repeat')==(rep if p['annual'] else None)
                               and (p['annual'] or x.get('at')==at.isoformat(timespec='seconds')) and not x.get('paused')),None)
            item=existing or self.reminders.add(at,label,rep if p['annual'] else None)
            items.append(item)
            details.append((describe_repeat(rep) if p['annual'] else 'один раз')+'; ближайшее — '+human(datetime.fromisoformat(item['at']),now))
        self.pending.pop(scope,None); self.save()
        return {'text':f'Напоминания настроены: {title}. '+'; '.join(details)+'.','items':items}
