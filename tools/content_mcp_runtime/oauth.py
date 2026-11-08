"""Interactive first login only; volatile storage. Persistence is host-owned."""
import time
import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (AuthorizationCodeResult, OAuthClientMetadata, OAuthClientInformationFull,
                            OAuthToken, OAuthMetadata, ProtectedResourceMetadata)
from callback import Callback
from policy import Policy, ContractError, RESOURCE, ISSUER, PRM, DISCOVERY, discover
from transport import GuardedTransport


class MemoryStorage:
    def __init__(self):self.tokens=self.client=None;self.expires_at=None
    async def get_tokens(self):return self.tokens
    async def get_client_info(self):return self.client
    async def set_client_info(self,value):
        if value.issuer!=ISSUER or value.client_secret:raise ContractError()
        self.client=value
    async def set_tokens(self,value):
        if value.token_type.lower()!='bearer' or not value.access_token:raise ContractError()
        if value.expires_in is not None and value.expires_in<=0:raise ContractError()
        self.tokens=value
        self.expires_at=time.time()+value.expires_in if value.expires_in is not None else None


async def authorize(on_url,inner=None,*,callback_timeout=300):
    storage=MemoryStorage()
    async with Callback() as listener:
        policy=Policy(redirect_uri=listener.redirect_uri)
        async def redirect(url):
            if not policy.oauth:raise ContractError()
            listener.arm(url,policy.oauth['authorization_endpoint'],policy.oauth.get('authorization_response_iss_parameter_supported',False))
            await on_url(url)
        async def callback():return AuthorizationCodeResult(**await listener.wait(callback_timeout))
        async def resource_guard(server,advertised):
            if server!=RESOURCE or advertised!=RESOURCE:raise ContractError()
        auth=OAuthClientProvider(server_url=RESOURCE,storage=storage,
            client_metadata=OAuthClientMetadata(redirect_uris=[listener.redirect_uri],client_name='Dewiar',
                token_endpoint_auth_method='none',grant_types=['authorization_code','refresh_token']),
            redirect_handler=redirect,callback_handler=callback,validate_resource_url=resource_guard)
        async with httpx2.AsyncClient(auth=auth,transport=GuardedTransport(policy,inner),follow_redirects=False,
                trust_env=False,timeout=httpx2.Timeout(10,connect=5),headers={'MCP-Protocol-Version':'2025-11-25'}) as client:
            report=await discover(client,policy)
        if storage.tokens is None or storage.client is None or not policy.oauth or report['state']!='tools_discovered':raise ContractError()
        return result_bundle(storage,policy,report)


def result_bundle(storage,policy,report):
    # No ID token / decoded claims: public account identity has not been verified.
    token={key:getattr(storage.tokens,key) for key in ('access_token','refresh_token','token_type','expires_in','scope')}
    bundle={'format':1,'resource':RESOURCE,'issuer':ISSUER,'client_info':storage.client.model_dump(mode='json',exclude_none=True),
            'tokens':token,'expires_at':storage.expires_at,'oauth_metadata':policy.oauth,'protected_metadata':policy.protected}
    return {'report':report,'credential':bundle}


async def refresh(bundle,inner=None):
    if bundle.get('format')!=1 or bundle.get('resource')!=RESOURCE or bundle.get('issuer')!=ISSUER:raise ContractError()
    storage=MemoryStorage()
    await storage.set_client_info(OAuthClientInformationFull.model_validate(bundle['client_info']))
    storage.tokens=OAuthToken.model_validate(bundle['tokens']);storage.expires_at=bundle['expires_at']
    if storage.client.token_endpoint_auth_method not in (None,'none'):raise ContractError()
    policy=Policy(refresh=True)
    async def no_login(*_):raise ContractError()  # never open consent / register during background refresh
    async def resource_guard(server,advertised):
        if server!=RESOURCE or advertised!=RESOURCE:raise ContractError()
    auth=OAuthClientProvider(server_url=RESOURCE,storage=storage,
        client_metadata=OAuthClientMetadata(redirect_uris=storage.client.redirect_uris,client_name='Dewiar',
            token_endpoint_auth_method='none',grant_types=['authorization_code','refresh_token'],scope=storage.tokens.scope),
        redirect_handler=no_login,callback_handler=no_login,validate_resource_url=resource_guard)
    async with httpx2.AsyncClient(transport=GuardedTransport(policy,inner),follow_redirects=False,
            trust_env=False,timeout=httpx2.Timeout(10,connect=5),headers={'MCP-Protocol-Version':'2025-11-25'}) as client:
        # Refresh is forbidden until both fresh metadata documents pass the guard.
        if (await client.get(PRM)).status_code!=200:raise ContractError()
        for url in DISCOVERY:
            response=await client.get(url)
            if response.status_code==200:break
            if response.status_code not in (404,405):raise ContractError()
        if policy.oauth is None:raise ContractError()
        await auth._initialize()
        auth.context.protected_resource_metadata=ProtectedResourceMetadata.model_validate(policy.protected)
        auth.context.oauth_metadata=OAuthMetadata.model_validate(policy.oauth)
        auth.context.auth_server_url=ISSUER
        # SDK _initialize does not restore expiry. Never treat expires_in as a new lifetime.
        auth.context.token_expiry_time=(storage.expires_at-30) if storage.expires_at else 1
        client.auth=auth
        report=await discover(client,policy)
    if report['state']!='tools_discovered' or storage.tokens is None:raise ContractError()
    return result_bundle(storage,policy,report)
