"""Reentrant per-workspace gate shared by content transactions and restore.

The OS lock is released on process exit. No stale lock-file deletion or PID
guessing. Separate workspaces remain independent.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

_registry={}
_registry_lock=threading.Lock()
_held=threading.local()


@contextmanager
def gate(root,timeout=5):
    root=Path(root);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    key=str(root.resolve())
    with _registry_lock:lock=_registry.setdefault(key,threading.RLock())
    if not lock.acquire(timeout=timeout):raise ValueError('Каталог контента занят. Повторите после завершения операции.')
    held=getattr(_held,'paths',None)
    if held is None:held={};_held.paths=held
    fd=None
    try:
        if key not in held:
            path=root/'.content.lock'
            fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
            deadline=time.monotonic()+timeout
            if os.name=='nt':
                import msvcrt
                if os.fstat(fd).st_size==0:os.write(fd,b'0')
                def acquire():os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                def acquire():fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            while True:
                try:acquire();break
                except (BlockingIOError,PermissionError):
                    if time.monotonic()>=deadline:raise ValueError('Каталог контента занят другим экземпляром. Повторите позже.') from None
                    time.sleep(.025)
            held[key]=0
        held[key]+=1
        try:yield
        finally:
            held[key]-=1
            if not held[key]:del held[key]
    finally:
        if fd is not None:os.close(fd)
        lock.release()
