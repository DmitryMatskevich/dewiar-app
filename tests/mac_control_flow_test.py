"""Real core direct commands, unreachable LLM, isolated exam launch log. No apps are controlled."""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from exam import Core


async def main():
    with tempfile.TemporaryDirectory(prefix='dewiar-app-control-') as folder:
        root=Path(folder)
        core=Core(dict(kind='openai',model='unavailable-test-only',base_url='http://127.0.0.1:9/v1'),root)
        original=subprocess.Popen
        bundle=Path(sys.argv[sys.argv.index('--app')+1]).resolve() if '--app' in sys.argv else ROOT/'dist/Dewiar.app'
        def spawn(command,*args,**kwargs):
            if '--bundle' in sys.argv and str(ROOT/'core/main.py') in command:
                command=[str(bundle/'Contents/MacOS/Dewiar'),'--core','--server']
            return original(command,*args,**kwargs)
        try:
            with patch('subprocess.Popen',side_effect=spawn):core.start()
            await core.connect()
            catalog=await core.call('tools')
            assert next(x for x in catalog if x['name']=='control_mac_app')['enabled']
            cases=[('Открой Калькулятор','open'),('Закрой Калькулятор','quit'),('Перенеси Safari на второй экран','move'),('Перемести курсор мыши на Safari','cursor')]
            for text,action in cases:
                reply,_=await core.say(text,timeout=12)
                assert 'рабочий стол не изменён' in reply,reply
                entries=[json.loads(line) for line in (root/'launch.jsonl').read_text().splitlines()]
                assert entries[-1]['tool']=='control_mac_app' and entries[-1]['args']['action']==action
            assert len(entries)==len(cases)
            print('OK: direct app/window/cursor commands through real core without LLM; exam log only, no desktop changes')
        finally:await core.stop()


if __name__=='__main__':asyncio.run(main())
