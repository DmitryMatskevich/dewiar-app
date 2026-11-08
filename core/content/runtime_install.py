"""Optional MCP-only venv installer. No models, main env or settings mutations."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid
from .mcp_helper import HelperError,call

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
REQUIREMENTS=ROOT/'requirements-content-mcp.txt'
MARKER='.dewiar-content-mcp.json'


def base_python():
    candidates=[shutil.which('python3.12'),'/opt/homebrew/bin/python3.12','/usr/local/bin/python3.12']
    if not getattr(sys,'frozen',False) and sys.version_info[:2]==(3,12):candidates.insert(0,sys.executable)
    return next((Path(p) for p in candidates if p and Path(p).is_file()),None)


def target(config_root):return Path(config_root)/'runtimes/content-mcp/2.3.0'


def available():return os.name=='posix' and REQUIREMENTS.is_file() and base_python() is not None


@contextmanager
def lock_file(path):
    # OS releases this lock on crash; no stale-PID guessing and no lock deletion race.
    import fcntl
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise HelperError('installation_busy') from None
        yield
    finally:os.close(fd)


def run(args,cancel,timeout):
    env={k:os.environ[k] for k in ('PATH','TMPDIR','TMP','TEMP','SYSTEMROOT') if k in os.environ}
    process=subprocess.Popen([str(a) for a in args],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,env=env,start_new_session=True)
    deadline=time.monotonic()+timeout
    interrupted=False
    try:
        while process.poll() is None:
            if cancel.is_set():raise HelperError('cancelled')
            if time.monotonic()>=deadline:raise HelperError('timeout')
            cancel.wait(.05)
        if process.returncode:raise HelperError('installation_failed')
    except BaseException:
        interrupted=True
        raise
    finally:
        if process.poll() is None or interrupted:
            # Only the process group created above (venv may spawn ensurepip).
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:pass
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=2)


def marker(path,data):
    tmp=path.with_name('.marker-'+uuid.uuid4().hex+'.tmp')
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


def install(config_root,cancel,progress,*,_base=None,_requirements=REQUIREMENTS,_run=run,_health=call):
    if os.name!='posix':raise HelperError('platform_not_qualified')
    base=Path(_base) if _base else base_python()
    if base is None or not base.is_file() or not _requirements.is_file():raise HelperError('runtime_unavailable')
    root=target(config_root);python=root/'bin/python';owner=root/MARKER
    raw=_requirements.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    expected={}
    for line in raw.decode().splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+',line):raise HelperError('invalid_requirements')
        name,version=line.split('==');expected[name]=version
    if expected.get('mcp')!='2.3.0':raise HelperError('invalid_requirements')
    prefix_code='import sys;from pathlib import Path;assert Path(sys.prefix).resolve()==Path('+repr(str(root))+').resolve() and sys.prefix!=sys.base_prefix;'
    version_code=prefix_code+'import importlib.metadata as m; expected='+repr(expected)+'; assert all(m.version(k)==v for k,v in expected.items())'
    with lock_file(root.parent/'install.lock'):
        if root.is_symlink() or owner.is_symlink():raise HelperError('foreign_runtime')
        if cancel.is_set():raise HelperError('cancelled')
        owned=False
        if owner.is_file():
            try:
                data=json.loads(owner.read_text());owned=data.get('format')==1 and data.get('provider')=='dewiar-content-mcp' and data.get('requirements_sha256')==digest
            except (ValueError,OSError):pass
        if root.exists() and not owned:
            # A manually prepared healthy environment is usable but never overwritten.
            try:
                _run([python,'-I','-B','-c',version_code],cancel,15)
                _health(python,'health',cancel=cancel,timeout=15)
            except HelperError:raise HelperError('foreign_runtime') from None
            progress('ready');return {'state':'ready','managed':False}
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        metadata={'format':1,'provider':'dewiar-content-mcp','requirements_sha256':digest,'state':'installing'}
        if owned and python.is_file():
            try:
                _run([python,'-I','-B','-c',version_code],cancel,15)
                _health(python,'health',cancel=cancel,timeout=15)
                marker(owner,{**metadata,'state':'ready'});progress('ready')
                return {'state':'ready','managed':True}
            except HelperError as e:
                if e.code in ('cancelled','timeout'):raise
        marker(owner,metadata)
        try:
            snapshot=root/'requirements.lock';snapshot_tmp=root/('.pins-'+uuid.uuid4().hex+'.tmp')
            fd=os.open(snapshot_tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(snapshot_tmp,snapshot)
            progress('preparing')
            _run([base,'-I','-B','-c','import sys; assert sys.version_info[:2]==(3,12)'],cancel,10)
            _run([base,'-I','-B','-m','venv',root],cancel,120)
            _run([python,'-I','-B','-c',prefix_code],cancel,10)
            progress('installing')
            _run([python,'-I','-B','-m','pip','--isolated','install','--disable-pip-version-check','--no-input',
                '--only-binary=:all:','--index-url','https://pypi.org/simple','--timeout','30','--retries','2','-r',snapshot],cancel,480)
            progress('checking')
            _run([python,'-I','-B','-m','pip','--isolated','check','--disable-pip-version-check'],cancel,30)
            _run([python,'-I','-B','-c',version_code],cancel,15)
            _health(python,'health',cancel=cancel,timeout=15)
            if cancel.is_set():raise HelperError('cancelled')
            marker(owner,{**metadata,'state':'ready'});progress('ready')
            return {'state':'ready','managed':True}
        except Exception:
            marker(owner,{**metadata,'state':'incomplete'})
            raise
