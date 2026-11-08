"""Check every request before network I/O, cap and close every response."""
import asyncio
import httpx2
from policy import ContractError


class GuardedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, policy, inner=None):
        self.policy = policy
        self.inner = inner or httpx2.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        url = str(request.url)
        cap = self.policy.request(request.method, url, request.headers, request.content)
        async def bounded():
            response = await self.inner.handle_async_request(request)
            try:
                if 300 <= response.status_code < 400: raise ContractError()
                declared = response.headers.get('content-length')
                if declared is not None and (not declared.isdecimal() or int(declared) > cap): raise ContractError()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > cap: raise ContractError()
                self.policy.response(url, response.status_code, bytes(data))
                headers = {key: val for key, val in response.headers.items() if key.lower() in (
                    'content-type', 'www-authenticate', 'mcp-session-id', 'mcp-protocol-version')}
                return httpx2.Response(response.status_code, content=bytes(data), headers=headers)
            finally: await response.aclose()
        return await asyncio.wait_for(bounded(), 15)

    async def aclose(self): await self.inner.aclose()
