"""Private local contact copies and explicit native import/create. No automatic outbound data."""
import copy
import hashlib
import datetime as dt
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import uuid
from pathlib import Path


def norm(value):return ' '.join(re.findall(r'[\w]+',unicodedata.normalize('NFC',str(value)).casefold().replace('ё','е')))
def name(row):return ' '.join(row.get(k,'').strip() for k in ('family_name','given_name','middle_name') if row.get(k,'').strip()) or row.get('organization','').strip()
def phone(value):return re.sub(r'\D','',value)
def fingerprint(row):return (norm(name(row)),tuple(sorted(phone(x) or x.casefold().strip() for x in row['phones'])),tuple(sorted(x.casefold() for x in row['emails'])),norm(row.get('organization','')),json.dumps(row.get('birthday'),sort_keys=True))

def clean(row,imported=False):
    out={}
    for key in ('given_name','family_name','middle_name','organization'):
        value=row.get(key,'')
        if not isinstance(value,str) or len(value)>300:raise ValueError('Проверьте поля имени и организации')
        out[key]=unicodedata.normalize('NFC',value.strip())
    if not imported and not name(out):raise ValueError('Укажите имя, фамилию или организацию')
    for key in ('phones','emails'):
        values=row.get(key,[])
        if not isinstance(values,list) or len(values)>50 or any(not isinstance(v,str) or len(v)>300 for v in values):raise ValueError('Некорректные телефоны или адреса')
        out[key]=list(dict.fromkeys(v.strip() for v in values if v.strip()))
    if not imported and any(not phone(v) or len(phone(v))<3 for v in out['phones']):raise ValueError('Проверьте телефон')
    if not imported and any(not re.fullmatch(r'[^\s@]+@[^\s@]+',v) for v in out['emails']):raise ValueError('Проверьте адрес электронной почты')
    out['birthday']=None
    if row.get('birthday'):
        b=row['birthday']
        try:
            year=int(b.get('year') or 2000);month=int(b['month']);day=int(b['day']);dt.date(year,month,day)
        except (ValueError,TypeError,KeyError):raise ValueError('Проверьте дату рождения') from None
        out['birthday']={'month':month,'day':day}
        if b.get('year'):out['birthday']['year']=year
    return out


def mention(text):
    # Full Russian FIO with a patronymic; known-card matches below also cover two-part/Latin names.
    text=unicodedata.normalize('NFC',text)
    stops={'Спроси','Скажи','Запомни','Создай','Добавь','Открой','Позвони','Встреться','Найди','Завтра','Сегодня','Если','Почему','Когда','Привет','Напомни','Напиши','Передай','Обсуди','Проверь','Пожалуйста'}
    words=re.findall(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ]?[а-яё]+)?",text)
    for i in range(len(words)-2):
        parts=words[i:i+3]
        if parts[0] in stops:continue
        if not re.search(r'(?:вич|вна|ична|ич|вичем|вной|ичем|ичны)$',parts[1]) and not re.search(r'(?:вич|вна|ична|ич|вичем|вной|ичем|ичны)$',parts[2]):continue
        candidate=' '.join(parts)
        if re.search(re.escape(parts[0])+r'\s+'+re.escape(parts[1])+r'\s+'+re.escape(parts[2]),text):return candidate
    explicit=re.search(r'\b(?:контакт(?:а|у|ом)?|человек(?:а|у|ом)?)\s+([А-ЯЁA-Z][а-яёa-z]+(?:[- ][А-ЯЁA-Z][а-яёa-z]+){1,2})',text)
    if explicit:return explicit[1]
    common={'Иван','Ивана','Пётр','Петр','Петра','Анна','Анны','Мария','Марии','Дмитрий','Дмитрия','Сергей','Сергея','Александр','Александра','Алексей','Алексея','Елена','Елены','Ольга','Ольги','Наталья','Натальи','Михаил','Михаила','Андрей','Андрея','Николай','Николая','Павел','Павла'}
    for i in range(len(words)-1):
        a,b=words[i:i+2]
        if ((a in common and re.search(r'(?:ов|ев|ин|ова|ева|ина|ский|ская|ко|ян|енко|ич|ук|юк|дзе|швили)$',b)) or (b in common and re.search(r'(?:ов|ев|ин|ова|ева|ина|ский|ская|ко|ян|енко|ич|ук|юк|дзе|швили)$',a))) and re.search(re.escape(a)+r'\s+'+re.escape(b),text):return a+' '+b
    return None


class ContactBook:
    def __init__(self,path,native=None):
        self.path=Path(path);self.native=native;self.lock=threading.RLock();self.preview=None;self.proposals={};self.last_scope=None
        try:self.data=json.loads(self.path.read_text())
        except FileNotFoundError:self.data={}
        except (OSError,ValueError):raise ValueError('Не удалось прочитать адресную книгу Dewiar. Сохраните файл contacts.json для восстановления; автоматически не перезаписываю.') from None
        if not isinstance(self.data,dict) or not isinstance(self.data.get('records',{}),dict):raise ValueError('Неверный формат адресной книги Dewiar')
        self.data.setdefault('records',{});self.data.setdefault('operations',{});self.data.setdefault('suggest',True)
        self.data.setdefault('dismissed',{})

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_name(self.path.name+'.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(self.data,stream,ensure_ascii=False)
            os.chmod(tmp,0o600);tmp.replace(self.path)
        finally:
            if tmp.exists():tmp.unlink()

    def records(self,query=''):
        with self.lock:
            tokens=norm(query).split();digits=phone(query) if query and not re.search(r'[А-Яа-яA-Za-z]',query) else ''
            return [copy.deepcopy(dict(r,id=rid,display_name=name(r))) for rid,r in self.data['records'].items()
                    if (digits and any(digits in phone(v) for v in r['phones'])) or all(t in norm(name(r)+' '+' '.join(r['phones'])+' '+' '.join(r['emails'])+' '+r['organization']) for t in tokens)]

    def state(self):
        with self.lock:return {'count':len(self.data['records']),'suggest':self.data['suggest'],
                              'permission':self.native.status() if self.native else {'state':'unavailable','granted':False}}

    def prepare_import(self):
        if not self.native:raise ValueError('Импорт из Контактов доступен только на Mac')
        rows=self.native.fetch()
        with self.lock:
            cards={}
            for row in rows:
                key=row.get('source_id')
                if not key:raise ValueError('У карточки отсутствует идентификатор')
                cards[key]=dict(clean(row,imported=True),source_id=key)
            token=secrets.token_urlsafe(24);self.preview={'token':token,'expires':time.time()+900,'cards':cards}
            return {'token':token,'contacts':[dict(r,display_name=name(r)) for r in cards.values()], 'count':len(cards),
                    'limited':self.native.status().get('limited',False)}

    def import_selected(self,token,ids,update=False):
        with self.lock:
            p=self.preview
            if not p or p['expires']<time.time() or not secrets.compare_digest(str(token),p['token']):raise ValueError('Список импорта устарел. Прочитайте Контакты заново.')
            if not isinstance(ids,list) or not ids or len(ids)>20000 or any(not isinstance(x,str) or x not in p['cards'] for x in ids):raise ValueError('Выберите карточки из показанного списка')
            if self.native:
                if not self.native.status().get('granted'):raise PermissionError('Доступ к Контактам отозван. Импорт остановлен.')
                accessible={r['source_id'] for r in self.native.fetch(identifiers=list(dict.fromkeys(ids)))}
                if set(ids)-accessible:raise ValueError('Доступные карточки изменились. Прочитайте список заново; импорт не выполнен.')
            prior=copy.deepcopy(self.data)
            added=updated=skipped=0
            by_source={};by_fingerprint={}
            for rid,record in self.data['records'].items():
                for source_id in record.get('source_ids',[]):by_source[source_id]=(rid,record)
                by_fingerprint.setdefault(fingerprint(record),[]).append((rid,record))
            for key in dict.fromkeys(ids):
                row=p['cards'][key];fp=fingerprint(row);existing=by_source.get(key)
                if not existing:
                    identical=[entry for entry in by_fingerprint.get(fp,[]) if not entry[1].get('source_ids')] if name(row) and (row['phones'] or row['emails']) else []
                    existing=identical[0] if len(identical)==1 else None
                if existing:
                    rid,old=existing
                    if key not in old.setdefault('source_ids',[]):old['source_ids'].append(key)
                    if update:
                        old_fp=fingerprint(old)
                        record=dict(clean(row,imported=True),source_ids=old['source_ids'],created=old.get('created'),updated=time.time())
                        self.data['records'][rid]=record
                        by_fingerprint[old_fp]=[(i,r) for i,r in by_fingerprint.get(old_fp,[]) if i!=rid]
                        by_fingerprint.setdefault(fp,[]).append((rid,record))
                        for source_id in record['source_ids']:by_source[source_id]=(rid,record)
                        updated+=1
                    else:
                        by_source[key]=(rid,old);skipped+=1
                else:
                    rid=uuid.uuid4().hex;record=dict(clean(row,imported=True),source_ids=[key],created=time.time(),updated=time.time())
                    self.data['records'][rid]=record;by_source[key]=(rid,record);by_fingerprint.setdefault(fp,[]).append((rid,record));added+=1
            try:self.save()
            except OSError:
                self.data=prior;raise ValueError('Не удалось сохранить импорт. Локальная адресная книга не изменена.') from None
            return {'added':added,'updated':updated,'skipped':skipped,'count':len(self.data['records'])}

    def create(self,payload,to_mac,request_id):
        row=clean(payload)
        if not isinstance(to_mac,bool):raise ValueError('Укажите место сохранения')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',request_id):raise ValueError('Отсутствует идентификатор создания')
        signature=hashlib.sha256(json.dumps([row,to_mac],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            old=self.data['operations'].get(request_id)
            if old:
                if old.get('fingerprint')!=signature:raise ValueError('Карточка изменилась после попытки сохранения. Откройте новую форму и проверьте существующие контакты.')
                if old.get('status')=='done':
                    if old['result']['id'] not in self.data['records']:raise ValueError('Эта локальная карточка уже удалена. Новая попытка требует новой формы.')
                    return copy.deepcopy(old['result'])
                raise ValueError('Предыдущая попытка могла создать карточку в macOS. Проверьте Контакты и импортируйте найденную карточку; автоматически повторно не создаю.')
            identical_local=[(rid,r) for rid,r in self.data['records'].items() if fingerprint(r)==fingerprint(row)]
            if to_mac:
                if not self.native or not self.native.status().get('granted'):raise PermissionError('Сначала разрешите доступ к Контактам macOS либо явно выберите сохранение только в Dewiar.')
                # Exact-card duplicate check; homonyms with different details remain distinct.
                candidates=self.native.fetch(query=row['family_name'] or row['given_name'] or row['organization'])
                identical=[r for r in candidates if fingerprint(clean(r,imported=True))==fingerprint(row)]
                if identical:raise ValueError('Такая карточка уже есть в Контактах macOS. Найдите её в импорте и скопируйте вместо создания дубликата.')
                self.data['operations'][request_id]={'status':'started','at':time.time(),'fingerprint':signature}
                try:self.save()
                except OSError:
                    self.data['operations'].pop(request_id,None)
                    raise ValueError('Не удалось подготовить локальную запись. Контакты macOS не изменялись.') from None
                try:source_id=self.native.create(row)
                except Exception:
                    raise ValueError('Создание в macOS не подтверждено. Проверьте Контакты перед повтором; эта попытка автоматически не повторяется.') from None
            else:source_id=None
            existing=identical_local[0] if len(identical_local)==1 else None
            rid=existing[0] if existing else uuid.uuid4().hex
            previous=existing[1] if existing else {}
            prior=copy.deepcopy(self.data)
            source_ids=list(dict.fromkeys(previous.get('source_ids',[])+([source_id] if source_id else [])))
            self.data['records'][rid]=dict(row,source_ids=source_ids,created=previous.get('created') or time.time(),updated=time.time())
            result={'id':rid,'display_name':name(row),'saved_mac':bool(source_id),'already_exists':bool(existing and not to_mac)}
            self.data['operations'][request_id]={'status':'done','result':result,'at':time.time(),'fingerprint':signature}
            try:self.save()
            except OSError:
                self.data=prior
                raise ValueError('Карточка не сохранена в Dewiar. Если включена запись в macOS, проверьте Контакты перед повтором.') from None
            return result

    def delete(self,rid):
        with self.lock:
            if rid not in self.data['records']:raise ValueError('Контакт не найден')
            prior=copy.deepcopy(self.data);self.data['records'].pop(rid)
            try:self.save()
            except OSError:self.data=prior;raise
            return {'deleted':True,'mac_unchanged':True}

    def proposal(self,text,scope,force=False,defer_native=False):
        if not self.data['suggest'] and not force:return None
        with self.lock:
            n=norm(text);who=mention(text)
            known=[dict(id=rid,**r) for rid,r in self.data['records'].items()]
            if not who:
                for r in known:
                    full=norm(name(r));base=' '.join(r.get(k,'') for k in ('family_name','given_name')).strip()
                    tokens=set(n.split())
                    if len(norm(base).split())>=2 and set(norm(base).split())<=tokens:who=base;break
                    if len(full.split())>=2 and set(full.split())<=tokens:who=name(r);break
            if not who:return None
            key=norm(who);scope=str(scope)
            if not force and key in self.data['dismissed'].get(scope,[]):return None
            old=self.proposals.get(scope)
            if old and old.get('key')==key and old['expires']>time.time():
                if force and defer_native and self.native and self.native.status().get('granted') and not old.get('searching'):
                    old.update(searching=True,lookup_error=False)
                return self.public_proposal(old) if force else None
            local=[r for r in known if set(key.split())<=set(norm(name(r)).split())]
            mac=[];lookup_error=False;searching=False
            if self.native and self.native.status().get('granted'):
                if defer_native:searching=True
                else:
                    try:
                        rows=self.native.fetch(query=who)
                        mac=[r for r in rows if set(key.split())<=set(norm(name(r)).split())]
                    except Exception:lookup_error=True
            p={'id':secrets.token_urlsafe(18),'name':who,'key':key,'scope':scope,'expires':time.time()+3600,
               'local':[{'id':r['id'],'name':name(r)} for r in local],
               'mac':[{'source_id':r['source_id'],'name':name(r)} for r in mac], 'lookup_error':lookup_error,'searching':searching}
            self.proposals[scope]=p;self.last_scope=scope
            return self.public_proposal(p)

    @staticmethod
    def public_proposal(p):return {k:copy.deepcopy(p[k]) for k in ('id','name','scope','local','mac','lookup_error','draft','searching') if k in p}

    def complete_lookup(self,pid):
        with self.lock:
            p=next((p for p in self.proposals.values() if p['id']==pid and p.get('searching') and p['expires']>time.time()),None)
            if not p:return None
            who,key=p['name'],p['key']
        error=False
        try:
            rows=self.native.fetch(query=who)
            matches=[{'source_id':r['source_id'],'name':name(r)} for r in rows if set(key.split())<=set(norm(name(r)).split())]
        except Exception:matches=[];error=True
        with self.lock:
            current=self.proposals.get(p['scope'])
            if not current or current['id']!=pid or not current.get('searching'):return None
            current.update(mac=matches,lookup_error=error,searching=False)
            return self.public_proposal(current)

    def fail_lookup(self,pid):
        with self.lock:
            p=next((p for p in self.proposals.values() if p['id']==pid and p.get('searching')),None)
            if not p:return None
            p.update(searching=False,lookup_error=True)
            return self.public_proposal(p)

    def pending(self,scope):
        with self.lock:
            p=self.proposals.get(str(scope))
            if not p or p['expires']<=time.time():return None
            p['local']=[{'id':rid,'name':name(r)} for rid,r in self.data['records'].items() if set(p['key'].split())<=set(norm(name(r)).split())]
            return self.public_proposal(p)

    def resolve_proposal(self,pid,action,source_id=None):
        with self.lock:
            p=next((p for p in self.proposals.values() if p['id']==pid and p['expires']>time.time()),None)
            if not p:raise ValueError('Предложение устарело. Повторите имя или откройте Контакты в настройках.')
            if action=='dismiss':
                self.data['dismissed'].setdefault(p['scope'],[]).append(p['key']);self.save();self.proposals.pop(p['scope'],None)
                return {'dismissed':True}
            if action=='draft':
                parts=p['name'].split()
                if len(parts)==3 and re.search(r'(?:вич|вна|ична|ич)$',parts[1]):draft=dict(family_name=parts[2],given_name=parts[0],middle_name=parts[1])
                elif len(parts)==3 and re.search(r'(?:вич|вна|ична|ич)$',parts[-1]):draft=dict(family_name=parts[0],given_name=parts[1],middle_name=parts[2])
                elif len(parts)==2 and re.search(r'(?:ов|ев|ин|ова|ева|ина|ский|ская|енко)$',parts[0]):draft=dict(family_name=parts[0],given_name=parts[1],middle_name='')
                else:draft=dict(family_name=parts[-1] if len(parts)>1 else '',given_name=parts[0],middle_name=' '.join(parts[1:-1]))
                p['draft']=draft
                return {'draft':draft}
            if action=='copy':
                if source_id not in [r['source_id'] for r in p['mac']]:raise ValueError('Выберите найденную карточку; совпадение имени само по себе не является подтверждением.')
                rows=self.native.fetch(identifiers=[source_id])
                row=next((r for r in rows if r['source_id']==source_id),None)
                if row is None:raise ValueError('Карточка недоступна или удалена. Повторите поиск.')
                # Resolve again against the current source, then use the regular explicit import path.
                self.preview={'token':secrets.token_urlsafe(24),'expires':time.time()+30,'cards':{source_id:dict(clean(row,imported=True),source_id=source_id)}}
                result=self.import_selected(self.preview['token'],[source_id]);self.proposals.pop(p['scope'],None);return result
            raise ValueError('Неизвестное действие с контактом')


_initialization_lock=threading.RLock()

def get_book(host):
    with _initialization_lock:
        return _get_book(host)

def _get_book(host):
    if not hasattr(host,'_contacts_book'):
        from settings import STATE_DIR
        from platform_info import MAC
        if MAC:
            from mac_contacts import MacContacts
            native=MacContacts()
        else:native=None
        host._contacts_book=ContactBook(STATE_DIR/'contacts.json',native)
    return host._contacts_book
