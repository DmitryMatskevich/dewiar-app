"""Test-only host for the real command route, without importing Engine dependencies.

Compile the current _command method verbatim from its AST. Only content_* is
allowed; every other Engine branch is unreachable. No production settings read.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import asyncio
import secrets
import threading
import json


def handler(workspace, tasks=None, oauth_runner=None, backup_fixture=False):
    path=Path(__file__).resolve().parents[1]/'core/engine_commands.py'
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CommandsMixin')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_command')
    scope={}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),scope)
    host=SimpleNamespace(workspace=SimpleNamespace(ensure=lambda:Path(workspace)),tasks=SimpleNamespace(tasks=tasks or {}))
    host.vault=SimpleNamespace(path=Path(workspace)/'test-config'/'vault.bin',key_path=Path(workspace)/'test-config'/'vault.key')
    backup_commands={'backup_info','backup_make','backup_restore','backup_inspect','backup_forget_pw'} if backup_fixture else set()
    if backup_fixture:
        from backup import Backup
        local=Path(workspace)/'_run';cfg=local/'config';state=local/'state';copies=local/'backups'
        cfg.mkdir(parents=True,exist_ok=True);state.mkdir(parents=True,exist_ok=True)
        host.s=SimpleNamespace(get=lambda section,key: str(copies) if (section,key)==('backup','dir') else False)
        host.vault.count=lambda:0
        host.backup=Backup(cfg,state,workspace,version='TEST isolated fixture')
        host.restart_core=lambda delay:None  # explicit fixture: never touches installed app
    if oauth_runner:
        import sys
        from content.oauth_service import Service
        host._content_oauth_service=Service(host.vault.path.parent,host.vault.key_path,runtime=sys.executable,runner=oauth_runner)
    def synthetic(system,prompt,cancel):
        payload=json.loads(prompt)
        if cancel.wait(1):return
        yield json.dumps({'replacements':[{'id':b['id'],'text':'ТЕСТ — предложение: '+b['text']} for b in payload['blocks']],
                          'issues':['ТЕСТ: синтетический результат проверяет механику, а не качество модели.']},ensure_ascii=False)
    host._content_test_provider=({'id':'synthetic','name':'ТЕСТ — синтетическая модель','model':'fixture','test_only':True},synthetic)
    def call(cmd,args):
        if cmd=='hello':return {'test_only':True}
        if not isinstance(cmd,str) or (not cmd.startswith('content_') and cmd not in backup_commands):raise ValueError('Тестовый стенд: команда отключена')
        return scope['_command'](host,cmd,args)
    def close():
        from content.archives import close as close_archives
        close_archives(host)
        service=getattr(host,'_content_oauth_service',None)
        if service:service.close()
    call.close=close
    return call


@contextmanager
def websocket(workspace,tasks=None,oauth_runner=None,backup_fixture=False):
    from bridge import Bridge
    from websockets.asyncio.server import serve
    bridge=Bridge(secrets.token_urlsafe(24),port=0);bridge.handler=handler(workspace,tasks,oauth_runner,backup_fixture)
    ready=threading.Event();state={}
    async def run():
        bridge.loop=asyncio.get_running_loop();state['stop']=asyncio.Event()
        async with serve(bridge._client,'127.0.0.1',0,compression=None,max_size=16*1024*1024) as service:
            state['port']=service.sockets[0].getsockname()[1];ready.set();await state['stop'].wait()
    thread=threading.Thread(target=lambda:asyncio.run(run()),daemon=True);thread.start()
    if not ready.wait(5):raise RuntimeError('Test bridge did not start')
    try:yield state['port'],bridge.token
    finally:
        bridge.handler.close()
        bridge.loop.call_soon_threadsafe(state['stop'].set);thread.join(5);bridge.pool.shutdown(wait=True)
