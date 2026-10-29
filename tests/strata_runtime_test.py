"""Official installed Strata CLI in its mock-engine mode; source/frozen Dewiar API client. No weights loaded."""
import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from strata_local import StrataLocal,runtime,GIB
from exam import Core


async def main():
    if not runtime():print('SKIP: official Strata CLI is not installed');return
    with tempfile.TemporaryDirectory(prefix='dewiar-strata-runtime-') as folder:
        root=Path(folder);manager=StrataLocal(root/'runtime')
        catalog=manager.status()['catalog']
        if not catalog:print('SKIP: installed Strata profile/tokenizer is missing');return
        original=subprocess.Popen
        def spawn(command,*args,**kwargs):
            if '--bundle' in sys.argv and str(ROOT/'core/main.py') in command:
                command=[str(ROOT/'dist/Dewiar.app/Contents/MacOS/Dewiar'),'--core','--server']
            elif any(str(x).endswith('/strata.py') for x in command):
                command=list(command)+['--engine','mock']
            return original(command,*args,**kwargs)
        core=None
        try:
            with patch('strata_local.psutil.virtual_memory',return_value=SimpleNamespace(available=100*GIB)),patch('subprocess.Popen',side_effect=spawn):
                manager.start(catalog[0]['id'])
            for _ in range(200):
                state=manager.status()
                if state['state']=='running':break
                if state.get('error'):raise AssertionError(state['error'])
                await asyncio.sleep(.1)
            assert manager.status()['state']=='running', manager.status()
            profile=manager.connection()
            core=Core(profile,root/'core')
            with patch('subprocess.Popen',side_effect=spawn):core.start()
            await core.connect()
            status=await core.call('strata_status')
            assert status['runtime'] and status['catalog']
            models=await core.call('llm_models',profile=profile)
            assert profile['model'] in models
            result=await core.call('llm_test',profile=profile)
            assert result['ok'] and 'mock engine' in result['reply'].lower(), result
            # Querying metadata must not start a second model in the Dewiar core.
            assert (await core.call('strata_status'))['state']=='stopped'
            print('OK: official Strata CLI headless mock, authenticated API, streaming through Dewiar, model list; no heavy weights or downloads')
        finally:
            if core:await core.stop()
            manager.stop()


if __name__=='__main__':asyncio.run(main())
