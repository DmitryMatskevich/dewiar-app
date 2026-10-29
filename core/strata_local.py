"""Strata API and an owned, loopback-only headless runtime. Never downloads model weights."""
import atexit
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import httpx
import psutil

GIB=1024**3


class MemoryShortage(ValueError):
    """A safe start was refused; carries data for the recovery dialog."""
    def __init__(self, available, required):
        self.available_gib = round(float(available) / GIB, 1)
        self.required_gib = round(float(required) / GIB, 1)
        self.deficit_gib = round(max(0.0, float(required) - float(available)) / GIB, 1)
        super().__init__(
            f"Недостаточно свободной памяти для безопасного старта: доступно {self.available_gib:.1f} ГБ, "
            f"оценка {self.required_gib:.1f} ГБ. Освободите память других моделей/программ и повторите. "
            "Они не закрываются автоматически."
        )

    def payload(self):
        from device import memory_snapshot
        snapshot = memory_snapshot(self.required_gib)
        # The process list is intentionally collected after the guard: this is
        # only shown when the model cannot fit and never closes anything itself.
        return {"available_gib": self.available_gib, "required_gib": self.required_gib,
                "deficit_gib": self.deficit_gib, "processes": snapshot["items"]}


def runtime(home=None,apps=None):
    home=Path(home or Path.home())
    roots=[home/'.local/share/strata/terminal/current']
    roots += [Path(p)/'Strata.app/Contents/Resources/control' for p in (apps or ['/Applications',home/'Applications'])]
    for root in roots:
        for name in ('python/bin/python3','python/bin/python'):
            py=root/name
            if py.is_file() and (root/'strata.py').is_file():return root.resolve(),py.absolute()
    return None


def discover_desktop_api():
    """Find a running packaged Strata Desktop host on macOS.

    Strata chooses a free port at launch and exposes it in the host process
    environment (``STRATA_PORT``).  The old 8080 placeholder therefore cannot
    work reliably.  Discovery is read-only and does not start or download a
    model.
    """
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "host/server/index.mjs" not in cmd or "strata" not in cmd.lower():
                continue
            try:
                env = proc.environ()
                port = int(str(env.get("STRATA_PORT", "")))
                host = str(env.get("STRATA_BIND", "127.0.0.1"))
            except (psutil.Error, OSError, ValueError, TypeError):
                continue
            if not (1 <= port <= 65535) or host not in ("127.0.0.1", "localhost", "::1"):
                continue
            return f"http://{host}:{port}/v1"
    except (psutil.Error, OSError):
        pass
    return ""


def api_profile(base,key=''):
    from settings import LLM_PRESETS
    u=urlparse(str(base).strip().rstrip('/'))
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('Укажите HTTP(S)-адрес API Strata без пароля и параметров')
    if u.path in ('','/'):u=u._replace(path='/v1')
    return dict(LLM_PRESETS['strata'],base_url=u.geturl(),api_key=str(key or ''))


def probe(base,key=''):
    from llm import list_models,LLMError
    requested = str(base or '').strip()
    discovered = discover_desktop_api()
    # Keep compatibility with the previous 8080 default, but prefer the live
    # packaged desktop host when it is present.  Explicit non-default URLs are
    # never rewritten.
    if discovered and (not requested or requested.lower() in ('auto', 'strata://', 'http://127.0.0.1:8080/v1')):
        requested = discovered
    p=api_profile(requested,key)
    try:ids=list_models(p,timeout=3)
    except LLMError as error:
        raise ValueError('API Strata недоступен. Проверьте адрес, ключ и загрузку модели. '+type(error).__name__) from error
    return dict(p,models=ids,model=ids[0] if ids else '',discovered=bool(discovered and p['base_url']==discovered),
                api_state='ready' if ids else 'no_model')


class StrataLocal:
    def __init__(self,state_dir,home=None):
        self.root=Path(state_dir)/'strata';self.home=Path(home or Path.home());self.lock=threading.RLock()
        self.proc=None;self.base='';self.key='';self.model='';self.error='';self.started=0;self.worker=None;self.stopping=False
        self.package_id=''
        atexit.register(self.stop)

    def _catalog(self):
        found=[]
        for path in sorted((self.home/'.local/share/strata/install').glob('strata-*.json')):
            try:
                cfg=json.loads(path.read_text())
                if not isinstance(cfg,dict):continue
                args=cfg.get('args')
                if not isinstance(args,list) or any(not isinstance(x,str) for x in args):continue
                def value(flag):
                    i=args.index(flag) if flag in args else -1
                    return args[i+1] if 0<=i<len(args)-1 else ''
                files=[Path(value(x)) for x in ('--native','--pack','--ple-gguf') if value(x)]
                missing=[p.name for p in files if not p.exists()]
                tokenizer=Path(cfg.get('tokenizer') or '')
                if not cfg.get('tokenizer') or not tokenizer.is_dir():missing.append('tokenizer')
                native=Path(value('--native')) if value('--native') else None
                # Disk footprint and RAM requirement are different facts.  A
                # model file lives on disk; the safety guard needs available
                # physical memory (RSS/working set) and a conservative runtime
                # overhead.  Keep ``required_gib`` as a compatibility alias
                # for older clients, but expose the meaning explicitly.
                disk_seen=set()
                def disk_size(item):
                    try:
                        if item.is_file():
                            key=str(item.resolve())
                            if key in disk_seen:return 0
                            disk_seen.add(key);return item.stat().st_size
                        if item.is_dir():
                            total=0
                            for x in item.rglob('*'):
                                if x.is_file():
                                    key=str(x.resolve())
                                    if key not in disk_seen:
                                        disk_seen.add(key);total+=x.stat().st_size
                            return total
                    except OSError:
                        return 0
                    return 0
                disk_bytes=sum(disk_size(p) for p in files)
                ram_required=(native.stat().st_size if native and native.is_file() else 0)+8*GIB
                found.append({'id':hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16],
                    'name':cfg.get('model_name') or path.stem,'config':str(path.resolve()),
                    'ready':bool(native and native.is_file() and not missing),'missing':missing,
                    'disk_gib':round(disk_bytes/GIB,1),
                    'ram_required_gib':round(ram_required/GIB,1),
                    'required_gib':round(ram_required/GIB,1)})
            except (OSError,ValueError,TypeError):continue
        return found

    def status(self):
        with self.lock:
            alive=bool(self.proc and self.proc.poll() is None)
            desktop_api=discover_desktop_api()
            mem=psutil.virtual_memory()
            mem_total=getattr(mem,'total',0) or 0
            mem_available=getattr(mem,'available',0) or 0
            return {'runtime':bool(runtime(self.home)),'catalog':self._catalog(),
                    'available_gib':round(mem_available/GIB,1),
                    'ram_total_gib':round(mem_total/GIB,1),
                    'ram_available_gib':round(mem_available/GIB,1),
                    'ram_used_gib':round(max(0,mem_total-mem_available)/GIB,1),
                    'desktop_api':desktop_api,'desktop_running':bool(desktop_api),
                    'state':'stopping' if self.stopping else 'loading' if alive and not self.model else 'running' if alive else 'stopped',
                    'base_url':self.base if alive else '', 'model':self.model if alive else '',
                    'owned':alive,'error':self.error}

    def connection(self):
        with self.lock:
            if not self.proc or self.proc.poll() is not None or not self.model:raise ValueError('Движок Strata ещё не готов')
            return dict(api_profile(self.base,self.key),model=self.model,strata_owned=True,strata_package_id=self.package_id)

    def start(self,model_id,context=4096):
        if isinstance(context,bool) or not isinstance(context,int) or not 1024<=context<=32768:
            raise ValueError('Контекст Strata: от 1024 до 32768 токенов')
        with self.lock:
            if self.worker and self.worker.is_alive() or self.proc and self.proc.poll() is None:
                raise ValueError('Движок Dewiar уже запускается или работает; сначала остановите его')
            rt=runtime(self.home)
            if not rt:raise ValueError('Не найден CLI Strata. Установите Strata или её официальный CLI; модели повторно скачивать не нужно')
            item=next((x for x in self._catalog() if x['id']==model_id),None)
            if not item or not item['ready']:raise ValueError('Нет готового пакета модели и tokenizer; настройте пакет в Strata')
            required=item['required_gib']*GIB+(context-1024)*65536
            available=psutil.virtual_memory().available
            if available<required:
                raise MemoryShortage(available,required)
            cfg=self._config(Path(item['config']),rt[0],context)
            self.package_id=model_id
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            self.key=secrets.token_urlsafe(32);self.base=f'http://127.0.0.1:{port}/v1'
            cfg.update(api_key=self.key,host='127.0.0.1',port=port,parallel=1,offline=True,max_queue=2,queue_timeout=30)
            path=self.root/'run.json';path.write_text(json.dumps(cfg),encoding='utf-8');path.chmod(0o600)
            self.model='';self.error='';self.stopping=False;self.started=time.monotonic()
            command=[str(rt[1]),str(rt[0]/'strata.py'),'serve','--profile',str(path),'--host','127.0.0.1','--port',str(port),'--offline','--shutdown-timeout','5']
            env={k:v for k,v in os.environ.items() if not k.startswith(('STRATA_','DEWIAR_','PYTHON','DYLD_','LD_')) and k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','HF_TOKEN','HUGGING_FACE_HUB_TOKEN')}
            env.update(PYTHONUNBUFFERED='1',PYTHONNOUSERSITE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
            log=self.root/'runtime.log';stream=log.open('wb');log.chmod(0o600)
            try:self.proc=subprocess.Popen(command,cwd=str(rt[0]),env=env,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            finally:stream.close()
            self.worker=threading.Thread(target=self._wait_ready,args=(self.proc,self.base,self.key),daemon=True)
            self.worker.start()
            return self.status()

    def _config(self,path,control,context):
        original=json.loads(path.read_text())
        # An install profile is data: never run a custom executable, hooks, MCP servers or preload variables.
        if original.get('exe')!='component:strata-engine':
            raise ValueError('Поддерживается официальный component:strata-engine; произвольные исполняемые файлы не запускаются')
        args=list(original['args'])
        for flag in ('--mtp','--spec','--vision'):
            while flag in args:
                i=args.index(flag);args.pop(i)
                if i<len(args) and not args[i].startswith('--'):args.pop(i)
        args+=['--spec','0']
        for flag in ('--max-context','--context','--ctx-size'):
            if flag in args:
                i=args.index(flag)
                if i+1<len(args):args[i+1]=str(context)
        if '--max-context' not in args:args+=['--max-context',str(context)]
        if '--expert-profile' in args:
            i=args.index('--expert-profile');replacement=control/'data/expert-profile.bin'
            if not replacement.is_file():raise ValueError('Не найден expert-profile.bin официального движка')
            args[i+1]=str(replacement)
        for flag in ('--pack','--native','--ple-gguf','--mtp'):
            if flag in args and not Path(args[args.index(flag)+1]).exists():
                raise ValueError('Отсутствует локальный файл пакета '+flag+'; автоматическая загрузка не выполняется')
        cfg={k:original[k] for k in ('exe','tokenizer','model_name','backend') if k in original}
        cfg.update(args=args,cwd=str(control),context=context)
        if original.get('env'):
            cfg['env']={k:str(v) for k,v in original['env'].items() if k in ('STRATA_METAL_STREAM','STRATA_PIN_BY_NEED')}
        return cfg

    def _wait_ready(self,proc,base,key):
        try:
            deadline=time.monotonic()+180
            while time.monotonic()<deadline and proc.poll() is None and not self.stopping:
                try:
                    health=httpx.get(base.removesuffix('/v1')+'/health',headers={'Authorization':'Bearer '+key},timeout=1)
                    if health.status_code!=200 or health.json().get('loaded') is not True:
                        time.sleep(.5);continue
                    r=httpx.get(base+'/models',headers={'Authorization':'Bearer '+key},timeout=1)
                    ids=[m['id'] for m in r.json().get('data',[])] if r.status_code==200 else []
                    if ids:
                        with self.lock:
                            if self.proc is proc and not self.stopping:self.model=ids[0]
                        return
                except (httpx.HTTPError,ValueError,KeyError):pass
                time.sleep(.5)
            with self.lock:
                if self.proc is proc and not self.stopping:self.error='Strata не загрузила модель за 180 секунд или завершилась. Подробности: state/strata/runtime.log'
        finally:
            if not self.model and self.proc is proc:self.stop()

    def stop(self):
        with self.lock:
            proc=self.proc
            if not proc:return
            self.stopping=True
        if proc.poll() is None:
            try:os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                proc.wait(timeout=3)
        with self.lock:
            if self.proc is proc:
                self.proc=None;self.model='';self.key='';self.base='';self.stopping=False
                try:(self.root/'run.json').unlink()
                except FileNotFoundError:pass
