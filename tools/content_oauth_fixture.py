"""Explicit local UI fixture, never imported by production commands."""
import threading
import time
import uuid
from content.mcp_helper import CredentialBundle,LoginResult,HelperError


class OAuthFixture:
    def __init__(self):self.base_url=None;self.pending={};self.lock=threading.Lock()
    def decide(self,ident,decision):
        with self.lock:
            state=self.pending.get(ident)
            if not state or decision not in ('approve','deny'):raise ValueError('Тестовый вход завершён')
            state['decision']=decision;state['event'].set()
    def exists(self,ident):
        with self.lock:return ident in self.pending
    def __call__(self,python,operation,**kwargs):
        if operation=='health':return {'state':'ready','sdk':'2.3.0','protocol':1,'can_generate':False}
        if operation=='authorize':
            ident=uuid.uuid4().hex;state={'event':threading.Event(),'decision':None}
            with self.lock:self.pending[ident]=state
            try:
                kwargs['on_authorization_url'](self.base_url+'/oauth-test/'+ident)
                deadline=time.monotonic()+300
                while not state['event'].wait(.05):
                    if kwargs['cancel'].is_set():raise HelperError('cancelled')
                    if time.monotonic()>deadline:raise HelperError('timeout')
                if state['decision']!='approve':raise HelperError('authorization_denied')
            finally:
                with self.lock:self.pending.pop(ident,None)
        elif operation!='refresh':raise HelperError('unsupported_operation')
        issuer='https://clerk.higgsfield.ai';resource='https://mcp.higgsfield.ai/mcp'
        value={'format':1,'resource':resource,'issuer':issuer,'client_info':{'client_id':'TEST-client','issuer':issuer},
            'tokens':{'access_token':'TEST-access-'+uuid.uuid4().hex,'refresh_token':'TEST-refresh-'+uuid.uuid4().hex,'token_type':'Bearer','expires_in':3600,'scope':'openid offline_access'},
            'expires_at':time.time()+3600,'oauth_metadata':{'issuer':issuer,'authorization_endpoint':issuer+'/oauth/authorize','token_endpoint':issuer+'/oauth/token'},'protected_metadata':{'resource':resource,'authorization_servers':[issuer]}}
        return LoginResult({'state':'tools_discovered','resource':resource,'protocol':'2025-11-25','tools':[],'can_generate':False},CredentialBundle(value))
