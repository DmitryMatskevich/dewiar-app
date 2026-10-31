"""Preload only the selected loopback model, without touching chat history."""
import ipaddress
import logging
import time
from urllib.parse import urlparse
import httpx

log = logging.getLogger(__name__)


def local_profile(p):
    if not p or not p.get('model') or p.get('cloud') or p['model'].endswith(':cloud'):
        return False
    if p.get('kind') not in ('openai', 'ollama'):
        return False
    host = urlparse(p.get('base_url', '')).hostname
    if host == 'localhost':return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


def signature(p):
    return tuple(p.get(k) for k in ('base_url', 'model', 'api_key', 'strata_package_id')) if p else ()


def preload(p, stop):
    """Native load first; a one-token completion also initializes inference kernels."""
    base = p['base_url'].rstrip('/')
    root = base.removesuffix('/v1')
    headers = {'Authorization': 'Bearer ' + p['api_key']} if p.get('api_key') else {}
    with httpx.Client(headers=headers, timeout=httpx.Timeout(5, read=300), follow_redirects=False) as client:
        if stop.is_set():return
        # Native Ollama loading does not generate a chat message.
        if p.get('kind') == 'ollama' or urlparse(base).port == 11434:
            r = client.post(root + '/api/generate', json={'model': p['model'], 'prompt': '', 'stream': False, 'keep_alive': '10m'})
            r.raise_for_status()
            return
        # LM Studio native metadata lets us preserve an already loaded context.
        metadata = client.get(root + '/api/v1/models')
        if metadata.status_code == 200:
            items = metadata.json().get('models', [])
            model = next((m for m in items if m.get('key') == p['model'].split('@')[0]), None)
            if model and not model.get('loaded_instances'):
                r = client.post(root + '/api/v1/models/load', json={'model': p['model'], 'context_length': 4096})
                r.raise_for_status()
        if stop.is_set():return
        r = client.post(base + ('/chat/completions' if base.endswith('/v1') else '/v1/chat/completions'), json={
            'model': p['model'], 'messages': [{'role':'user','content':'Hi'}],
            'max_tokens': 1, 'stream': False, 'temperature': 0,
            'chat_template_kwargs': {'enable_thinking': False}, 'strata_mcp': False})
        r.raise_for_status()
        data = r.json()
        if not data.get('choices'):
            raise ValueError('Сервер не подтвердил готовность модели')


class ModelWarmup:
    def __init__(self, engine):
        self.engine = engine
        self.last = None
        self.at = 0
        self.ready = False

    def selected(self):
        return self.engine.s.active_llm()

    def step(self):
        e = self.engine
        p = self.selected()
        if e.stopping.is_set():return
        if not local_profile(p):
            if e.status.pop('model_warmup', None):e.emit('status', status=e.status)
            self.last, self.ready = None, False
            return
        key = signature(p)
        # Retry transient startup errors; refresh Ollama's idle residency.
        interval = 240 if self.ready else 30
        if key == self.last and time.monotonic() - self.at < interval:return
        self.last, self.at, self.ready = key, time.monotonic(), False
        label = p.get('model')
        e._load('llm_warmup', 'Загружаю и прогреваю ' + label)
        e.status['model_warmup'] = {'state':'loading', 'model':label}
        e.emit('status', status=e.status)
        try:
            # Owned Strata ports/keys change across restarts. Resolve its saved package.
            if p.get('strata_owned') and p.get('strata_package_id'):
                from strata_local import StrataLocal
                from settings import STATE_DIR
                if not hasattr(e, 'strata'):e.strata = StrataLocal(STATE_DIR)
                manager = e.strata
                state = manager.status()
                if not state['owned']:manager.start(p['strata_package_id'], 4096)
                end = time.monotonic() + 300
                while manager.status()['state'] == 'loading':
                    if e.stopping.wait(.5):return
                    if time.monotonic() >= end:raise ValueError('Истекло время загрузки Strata')
                connection = manager.connection()
                llm = e.s.snapshot()['llm']
                profiles = llm.get('profiles', {})
                for pid, saved in profiles.items():
                    if signature(saved) == key:
                        profiles[pid] = dict(saved, **connection)
                e.s.update({'llm': {'profiles': profiles}})
                e.emit('settings', settings=e.s.snapshot())
                p = dict(p, **connection)
                self.last = signature(p)
            if signature(self.selected()) != signature(p):return
            if urlparse(p['base_url']).port == 1234:
                from llm import lmstudio_start
                url, _ = lmstudio_start()
                if url and url != p['base_url']:
                    llm = e.s.snapshot()['llm']; profiles = llm.get('profiles', {})
                    for pid, saved in profiles.items():
                        if signature(saved) == signature(p):profiles[pid] = dict(saved, base_url=url)
                    e.s.update({'llm': {'profiles': profiles}})
                    e.emit('settings', settings=e.s.snapshot())
                    p = dict(p, base_url=url); self.last = signature(p)
            preload(p, e.stopping)
            if e.stopping.is_set() or signature(self.selected()) != signature(p):return
            self.ready = True
            e.status['model_warmup'] = {'state':'ready', 'model':label}
        except Exception as error:
            log.info('Прогрев %s: %s', label, type(error).__name__)
            if signature(self.selected()) == signature(p):
                e.status['model_warmup'] = {'state':'error', 'model':label, 'message':'Не удалось прогреть модель. Проверьте локальный сервер и свободную память.'}
        finally:
            e._loaded('llm_warmup')
            e.emit('status', status=e.status)
        if self.ready:e.probe_tools_bg()

    def run(self):
        while not self.engine.stopping.is_set():
            try:self.step()
            except Exception:
                log.exception('Не удалось подготовить локальную модель')
            if self.engine.stopping.wait(5):break
