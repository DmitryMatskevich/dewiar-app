"""Explicit desktop canvas attention. No model/network calls or general-turn state."""
import threading,time,uuid
LOCK=threading.RLock()
def data(engine):
    if not hasattr(engine,'_canvas_scope'):engine._canvas_scope={'mode':'general','active':None,'pinned':None,'seen':0,'inbox':{}}
    return engine._canvas_scope

def target(engine):
    s=data(engine)
    if s['mode']=='general' or not s['active']:return None
    if time.monotonic()-s['seen']>15:raise ValueError('Связь с холстом потеряна. Откройте редактор или выберите общий разговор.')
    return s['pinned'] if s['mode']=='pinned' else s['active']

def submit(engine,text):
    with LOCK:
        s=data(engine);identifier=target(engine)
        if not identifier:return False
        if not isinstance(text,str) or not text.strip() or len(text)>2000:raise ValueError('Введите команду холста до 2000 знаков.')
        if s['inbox'].get(identifier):raise ValueError('Холст занят. Завершите текущую команду или предложение.')
        s['inbox'][identifier]={'id':uuid.uuid4().hex,'text':text,'taken':False}
        return True

def command(engine,cmd,a):
    import canvases as C
    with LOCK:
        s=data(engine)
        if cmd=='canvas_scope_active':
            identifier=a.get('canvas_id');C.read(engine,identifier);s['active']=identifier;s['seen']=time.monotonic()
        elif cmd=='canvas_scope_closed':
            if s['active']==a.get('canvas_id'):s['active']=None
        elif cmd=='canvas_scope_mode':
            mode=a.get('mode')
            if mode not in ('follow','general','pinned'):raise ValueError('Неверный режим внимания')
            if mode=='pinned' and not s['active']:raise ValueError('Сначала откройте холст.')
            s['mode']=mode
            if mode=='pinned':s['pinned']=s['active']
        elif cmd=='canvas_scope_begin':
            identifier=a.get('canvas_id');row=s['inbox'].get(identifier)
            if row and row['id']!=a.get('request_id'):raise ValueError('Холст занят другой командой.')
            if not row:s['inbox'][identifier]={'id':uuid.uuid4().hex,'text':'','taken':True}
        elif cmd=='canvas_scope_stop':
            event=getattr(engine,'_canvas_cancel',{}).get(a.get('canvas_id'))
            if event:event.set()
            s['inbox'].pop(a.get('canvas_id'),None)
        elif cmd=='canvas_scope_take':
            row=s['inbox'].get(a.get('canvas_id'))
            if row and not row['taken']:row['taken']=True;return row
            return None
        elif cmd=='canvas_scope_done':
            s['inbox'].pop(a.get('canvas_id'),None)
        elif cmd=='canvas_scope_submit':
            if not submit(engine,str(a.get('text') or '')[:2000]):raise ValueError('Откройте холст')
        elif cmd!='canvas_scope_state':raise ValueError('Неизвестная команда внимания')
        identifier=target(engine);result={'mode':s['mode'],'canvas_id':identifier,'title':'','messages':[],'busy':False}
        if identifier:
            from canvas_chat import state
            folder,doc=C.read(engine,identifier);result.update(title=doc['title'],messages=state(folder,doc)['messages'],busy=bool(s['inbox'].get(identifier)))
        return result
