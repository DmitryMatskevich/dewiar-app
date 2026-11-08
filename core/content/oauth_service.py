"""Trusted UI lifecycle. Workers own private helper results; UI sees status only."""
from pathlib import Path
import threading
from . import mcp_helper,oauth_store
from .oauth_vault import CredentialVault
from interface_origin import require_interface

COMMANDS={'content_oauth_list','content_oauth_get','content_oauth_create','content_oauth_update','content_oauth_start',
          'content_oauth_cancel','content_oauth_disconnect','content_oauth_runtime','content_oauth_check_runtime',
          'content_oauth_install_runtime','content_oauth_cancel_install'}
_registry_lock=threading.Lock()


class Service:
    def __init__(self,config_root,key_path,*,runtime=None,runner=None):
        self.config_root=Path(config_root);self.installation=None;self.install_state='not_started';self.install_error=None
        self.vault=CredentialVault(Path(config_root)/'content-oauth-vault',key_path)
        self.runtime=Path(runtime) if runtime else Path(config_root)/'runtimes/content-mcp/2.3.0'/('Scripts/python.exe' if __import__('os').name=='nt' else 'bin/python')
        self.runner=runner or mcp_helper.call;self.active={};self.lock=threading.RLock();self.runtime_checked=False

    def runtime_status(self):
        from . import runtime_install
        present=self.runtime.is_file()
        return {'installed':present,'ready':present and self.runtime_checked and not self.installation,'sdk':'2.3.0','can_generate':False,
                'installing':self.installation is not None,
                'can_install':runtime_install.available() and self.runner is mcp_helper.call,'install_state':self.install_state,'install_error':self.install_error,
                'test_only':self.runner is not mcp_helper.call,
                'message':'Отдельный модуль MCP проверен.' if present and self.runtime_checked else ('Модуль найден. Проверьте его перед входом.' if present else 'Модуль подключения не установлен. Для установки нужен Python 3.12; модели Dewiar сохраняются.')}

    def check_runtime(self):
        if self.installation:raise ValueError('Дождитесь завершения установки')
        with self.lock:self.runtime_checked=False
        self.runner(self.runtime,'health',timeout=15)
        with self.lock:self.runtime_checked=True
        return self.runtime_status()

    def install_runtime(self):
        from . import runtime_install
        with self.lock:
            if self.installation:return self.runtime_status()
            if self.active:raise ValueError('Сначала завершите операции подключения')
            if not self.runtime_status()['can_install']:raise ValueError('Для установки нужен Python 3.12 и поддержанная платформа')
            state={'cancel':threading.Event()};self.installation=state;self.install_state='preparing';self.install_error=None;self.runtime_checked=False
            def progress(value):
                with self.lock:self.install_state=value
            def work():
                try:
                    runtime_install.install(self.config_root,state['cancel'],progress)
                    with self.lock:self.runtime_checked=True
                except Exception as e:
                    with self.lock:
                        self.install_state='cancelled' if state['cancel'].is_set() else 'failed'
                        code=e.code if isinstance(e,mcp_helper.HelperError) else 'installation_failed'
                        self.install_error=code if code in {'installation_failed','foreign_runtime','timeout','runtime_unavailable','installation_busy','cancelled'} else 'installation_failed'
                finally:
                    with self.lock:self.installation=None
            state['thread']=threading.Thread(target=work,daemon=True,name='content-mcp-install');state['thread'].start()
        return self.runtime_status()

    def cancel_install(self):
        with self.lock:
            state=self.installation
            if state:state['cancel'].set()
        if state:state['thread'].join(5)
        return self.runtime_status()

    @staticmethod
    def key(store,ident):return str(store.path.resolve()),ident

    def get(self,store,ident):
        oauth_store.check_local_credentials(store,self.vault,ident)
        row=oauth_store.get(store,ident)
        with self.lock:
            job=self.active.get(self.key(store,ident))
            live=job and not job['cancel'].is_set() and row['state'] in oauth_store.BUSY
            row['authorization_url']=job.get('url') if live else None
            row['intent_id']=job['intent_id'] if live else None
        row['events']=oauth_store.history(store,ident)
        return row

    def start(self,store,args):
        with self.lock:
            if not self.runtime_checked or self.installation:raise ValueError('Сначала проверьте отдельный модуль MCP')
            job=oauth_store.begin(store,args.get('id'),args.get('expected_revision'),args.get('operation'),args.get('request_key'))
            key=self.key(store,job['id'])
            if not job['created']:return self.get(store,job['id'])
            state={'intent_id':job['intent_id'],'cancel':threading.Event(),'url':None,'store':store,'id':job['id']}
            self.active[key]=state
            def on_url(url):
                # Validated by mcp_helper before this callback; never persisted or logged.
                with self.lock:
                    if state['cancel'].is_set() or self.active.get(key) is not state:raise mcp_helper.HelperError('cancelled')
                    state['url']=url
            def work():
                try:
                    operation=args['operation']
                    value=oauth_store.credential(store,self.vault,job['id'],job['intent_id']) if operation=='refresh' else None
                    result=self.runner(self.runtime,operation,cancel=state['cancel'],timeout=340 if operation=='authorize' else 55,
                        **({'on_authorization_url':on_url} if operation=='authorize' else {'credential':value}))
                    with self.lock:
                        if state['cancel'].is_set():raise mcp_helper.HelperError('cancelled')
                        oauth_store.finish(store,self.vault,job['id'],job['intent_id'],result)
                except Exception as e:
                    oauth_store.fail(store,job['id'],job['intent_id'],e.code if isinstance(e,mcp_helper.HelperError) else 'operation_failed')
                finally:
                    with self.lock:
                        state['url']=None
                        if self.active.get(key) is state:self.active.pop(key,None)
            thread=threading.Thread(target=work,daemon=True,name='content-oauth');state['thread']=thread;thread.start()
        return self.get(store,job['id'])

    def cancel(self,store,ident,intent):
        with self.lock:
            state=self.active.get(self.key(store,ident))
            if not state or state['intent_id']!=intent:raise ValueError('Операция уже завершена или принадлежит другому окну')
            state['cancel'].set();state['url']=None
            result=oauth_store.fail(store,ident,intent,'cancelled')
        state['thread'].join(3)
        return self.get(store,ident)

    def disconnect(self,store,ident,expected):
        with self.lock:
            result=oauth_store.disconnect(store,self.vault,ident,expected)
            state=self.active.get(self.key(store,ident))
            if state:state['cancel'].set();state['url']=None
        if state:state['thread'].join(3)
        return self.get(store,ident)

    def close(self):
        self.cancel_install()
        with self.lock:states=list(self.active.values())
        for state in states:
            try:self.cancel(state['store'],state['id'],state['intent_id'])
            except ValueError:pass


def service(host):
    with _registry_lock:
        if not hasattr(host,'_content_oauth_service'):
            vault=getattr(host,'vault',None)
            if vault is None:raise ValueError('Хранилище credentials недоступно в этом экземпляре')
            host._content_oauth_service=Service(vault.path.parent,vault.key_path)
        return host._content_oauth_service


def command(host,store,cmd,args):
    require_interface()
    fields={'content_oauth_list':{'archived'},'content_oauth_get':{'id'},'content_oauth_create':{'label','request_key'},
        'content_oauth_update':{'id','expected_revision','label','archived'},'content_oauth_start':{'id','expected_revision','operation','request_key'},
        'content_oauth_cancel':{'id','intent_id'},'content_oauth_disconnect':{'id','expected_revision'},'content_oauth_runtime':set(),'content_oauth_check_runtime':set(),
        'content_oauth_install_runtime':set(),'content_oauth_cancel_install':set()}
    if not isinstance(args,dict) or cmd not in fields or set(args)-fields[cmd]:raise ValueError('Некорректные параметры подключения')
    svc=service(host)
    if cmd=='content_oauth_runtime':return svc.runtime_status()
    if cmd=='content_oauth_install_runtime':return svc.install_runtime()
    if cmd=='content_oauth_cancel_install':return svc.cancel_install()
    if cmd=='content_oauth_check_runtime':
        try:return svc.check_runtime()
        except mcp_helper.HelperError:raise ValueError('Отдельный модуль MCP не прошёл проверку. Проверьте его установку.') from None
    if cmd=='content_oauth_list':
        oauth_store.check_local_credentials(store,svc.vault)
        return oauth_store.listing(store,args.get('archived',False))
    if cmd=='content_oauth_get':return svc.get(store,args.get('id'))
    if cmd=='content_oauth_create':return oauth_store.create(store,args.get('label'),args.get('request_key'))
    if cmd=='content_oauth_update':return oauth_store.update(store,args.get('id'),args.get('expected_revision'),**{k:args[k] for k in ('label','archived') if k in args})
    if cmd=='content_oauth_start':return svc.start(store,args)
    if cmd=='content_oauth_cancel':return svc.cancel(store,args.get('id'),args.get('intent_id'))
    if cmd=='content_oauth_disconnect':return svc.disconnect(store,args.get('id'),args.get('expected_revision'))
