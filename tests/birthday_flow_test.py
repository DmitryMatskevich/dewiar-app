"""Birthday dialogue over real core WebSocket; optional frozen Mac application."""
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from exam import Core


async def main():
    with tempfile.TemporaryDirectory(prefix='dewiar-birthday-') as folder:
        root=Path(folder)
        core=Core({'kind':'openai','model':'unavailable-test-only','base_url':'http://127.0.0.1:9/v1'},root)
        original=subprocess.Popen
        def spawn(command,*args,**kwargs):
            if '--bundle' in sys.argv and str(ROOT/'core/main.py') in command:
                command=[str(ROOT/'dist/Dewiar.app/Contents/MacOS/Dewiar'),'--core','--server']
            return original(command,*args,**kwargs)
        try:
            with patch('subprocess.Popen',side_effect=spawn):core.start()
            await core.connect()
            first,_=await core.say('У Анны день рождения 15 мая',timeout=10)
            assert 'ежегодными' in first,first
            assert await core.call('reminders')==[], 'Mention creates a reminder without consent'
            reply,_=await core.say('Да, каждый год в сам день и за неделю',timeout=10)
            items=await core.call('reminders')
            assert len(items)==2,(reply,items)
            assert {x['repeat']['offset_days'] for x in items}=={0,7}
            assert all(x['repeat']['kind']=='yearly' and 'каждый год' in x['repeat_text'] for x in items)
            assert all(x['repeat']['time']=='09:00' for x in items), 'Default birthday time is not 09:00'
            month=await core.call('calendar_month',year=int(items[0]['at'][:4]),month=5)
            assert sum(x.get('type')=='reminder' for day in month['days'].values() for x in day)==2
            await core.say('У Пети день рождения 29 февраля',timeout=10)
            question,_=await core.say('Ежегодно в сам день в 10:00',timeout=10)
            assert 'невисокосный' in question and len(await core.call('reminders'))==2
            result,_=await core.say('1 марта',timeout=10)
            items=await core.call('reminders')
            assert len(items)==3,(result,items)
            pete=next(x for x in items if 'Пети' in x['text'])
            assert pete['repeat']['leap_day']=='mar1' and pete['repeat']['day']==29,(result,items)
            await core.say('У Ольги день рождения 20 июня',timeout=10)
            await core.say('Не нужно',timeout=10)
            assert len(await core.call('reminders'))==3
            await core.say('У Ивана день рождения 1 января',timeout=10)
            result,_=await core.say('Один раз за день в 12:30',timeout=10)
            items=await core.call('reminders')
            assert len(items)==4 and next(x for x in items if 'Ивана' in x['text'])['repeat'] is None,(result,items)
            print('OK: real core birthday question, explicit consent, annual/once, multiple offsets, calendar display, leap choice, decline; no model required')
        finally:await core.stop()


if __name__=='__main__':asyncio.run(main())
