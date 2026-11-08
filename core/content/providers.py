"""Explicit configured LLM profile. No model install, settings mutation or fallback."""
import hashlib
import time
from urllib.parse import urlparse


def profiles(host):
    test=getattr(host,'_content_test_provider',None)
    if test:return [test[0]]
    configured=host.s.get('llm','profiles') or {}
    return [{'id':pid,'name':str(p.get('name') or pid),'model':str(p.get('model') or ''),'test_only':False}
            for pid,p in configured.items() if p.get('model')]


def selected(host,pid):
    test=getattr(host,'_content_test_provider',None)
    if test:
        if pid!=test[0]['id']:raise ValueError('Тестовая модель не найдена')
        return test
    if not isinstance(pid,str):raise ValueError('Выберите модель')
    profile=(host.s.get('llm','profiles') or {}).get(pid)
    if not profile or not profile.get('model'):raise ValueError('Профиль модели не настроен')
    import modelrouter, egress
    if not modelrouter.in_schedule(profile) or modelrouter.blocked(profile):raise ValueError('Модель сейчас недоступна по расписанию или лимиту')
    hostname=urlparse(profile.get('base_url') or '').hostname
    if egress.local_only() and not egress.local_host(hostname):raise ValueError('Внешняя модель запрещена режимом «только локально»')
    egress.check_dewiar_host(hostname)
    p=dict(profile,_profile_id=pid,max_tokens=6000,temperature=0.2,timeout=60,_routing_attempt=True)
    p.pop('_route_candidates',None);p.pop('_continue_response',None)
    descriptor={'id':pid,'name':str(p.get('name') or pid),'model':str(p['model']),'test_only':False,
                'endpoint_hash':hashlib.sha256(str(p.get('base_url') or '').encode()).hexdigest(),
                'max_tokens':6000,'temperature':'0.2'}
    def run(system,prompt,cancel):
        from llm import stream_chat
        yield from stream_chat(dict(p,_routing_deadline=time.monotonic()+120),system,[{'role':'user','content':prompt}],cancel=cancel)
    return descriptor,run
