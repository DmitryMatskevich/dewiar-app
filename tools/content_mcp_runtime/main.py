"""One bounded operation per isolated Python process; stdout is private IPC.

Invoke with -I -B. Never import app/core, user settings or the model runtime.
"""
import asyncio
import importlib.metadata
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from callback import CallbackDenied
VERSION = 1
MAX_LINE = 256 * 1024
logging.disable(logging.CRITICAL)


def emit(value):
    raw = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(raw) > MAX_LINE: raise ValueError()
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


async def execute(operation, ident, credential=None):
    if importlib.metadata.version('mcp') != '2.3.0': raise ImportError()
    from mcp.client.auth import OAuthClientProvider  # proves the SDK import isn't core/mcp.py
    if operation == 'health':
        return {'state': 'ready', 'sdk': '2.3.0', 'protocol': VERSION, 'can_generate': False}
    if operation == 'authorize':
        from oauth import authorize
        async def on_url(url):emit({'v':VERSION,'id':ident,'event':'authorization_url','url':url})
        return await asyncio.wait_for(authorize(on_url),330)
    if operation == 'refresh':
        from oauth import refresh
        return await asyncio.wait_for(refresh(credential),45)
    import httpx2
    from policy import Policy, discover
    from transport import GuardedTransport
    policy = Policy()
    async with httpx2.AsyncClient(transport=GuardedTransport(policy), follow_redirects=False,
            trust_env=False, timeout=httpx2.Timeout(10, connect=5)) as client:
        return await asyncio.wait_for(discover(client, policy), 45)


def main():
    ident = None
    try:
        raw = sys.stdin.buffer.readline(MAX_LINE + 1)
        if len(raw) > MAX_LINE or not raw.endswith(b'\n'): raise ValueError()
        from policy import decode
        frame = decode(raw)
        fields={'v','id','operation'}|({'credential'} if frame.get('operation')=='refresh' else set())
        if set(frame) != fields or type(frame['v']) is not int or frame['v'] != VERSION: raise ValueError()
        import re
        if not isinstance(frame['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', frame['id']): raise ValueError()
        ident = frame['id']
        if frame['operation'] not in ('health', 'discover', 'authorize', 'refresh'): raise ValueError()
        result = asyncio.run(execute(frame['operation'], ident, frame.get('credential')))
        emit({'v': VERSION, 'id': ident, 'ok': True, 'result': result})
    except (ImportError, importlib.metadata.PackageNotFoundError):
        emit({'v': VERSION, 'id': ident, 'ok': False, 'error': 'runtime_unavailable'})
    except CallbackDenied:
        emit({'v': VERSION, 'id': ident, 'ok': False, 'error': 'authorization_denied'})
    except TimeoutError:
        emit({'v': VERSION, 'id': ident, 'ok': False, 'error': 'timeout'})
    except BaseException:
        emit({'v': VERSION, 'id': ident, 'ok': False, 'error': 'operation_failed'})


if __name__ == '__main__': main()
