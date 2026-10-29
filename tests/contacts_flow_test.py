"""Contact mention and explicit local creation through real isolated core. Native PIM is disabled in exam."""
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
 with tempfile.TemporaryDirectory(prefix='dewiar-contacts-flow-') as tmp:
  core=Core(dict(kind='openai',model='unavailable-test-only',base_url='http://127.0.0.1:9/v1'),Path(tmp))
  original=subprocess.Popen
  bundle=Path(sys.argv[sys.argv.index('--app')+1]).resolve() if '--app' in sys.argv else ROOT/'dist/Dewiar.app'
  def spawn(command,*args,**kwargs):
   if '--bundle' in sys.argv and str(ROOT/'core/main.py') in command:command=[str(bundle/'Contents/MacOS/Dewiar'),'--core','--server']
   return original(command,*args,**kwargs)
  try:
   with patch('subprocess.Popen',side_effect=spawn):core.start()
   await core.connect()
   first,_=await core.say('Иванов Иван Иванович',timeout=10)
   assert 'Создать контакт' in first,first
   pending=await core.call('contacts_pending');assert pending['name']=='Иванов Иван Иванович'
   assert not (await core.call('contacts_list'))['items']
   reply,_=await core.say('Да',timeout=10)
   assert 'Проверьте данные' in reply,reply
   assert (await core.call('contacts_list'))['total']==0,'Voice yes writes before card review'
   assert (await core.call('contacts_pending'))['draft']['given_name']=='Иван'
   draft=await core.call('contacts_proposal_action',id=pending['id'],action='draft')
   assert draft['draft']['given_name']=='Иван'
   payload=dict(contact=dict(draft['draft'],phones=['+70000000001'],emails=['test@example.test']),to_mac=False,request_id='flow-request-000001')
   rejected=False
   try:await core.call('contacts_create',**payload)
   except Exception:rejected=True
   assert rejected,'No confirmation required'
   created=await core.call('contacts_create',**payload,confirmed=True)
   assert not created['saved_mac']
   assert (await core.call('contacts_create',**payload,confirmed=True))['id']==created['id']
   assert (await core.call('contacts_list'))['total']==1
   assert (await core.call('contacts_get',id=created['id']))['phones']==['+70000000001']
   await core.call('contacts_delete',id=created['id'],confirmed=True)
   assert (await core.call('contacts_list'))['total']==0
   await core.call('contacts_proposal_action',id=pending['id'],action='dismiss')
   assert await core.call('contacts_pending') is None
   question='Скажи, занят ли Сидоров Сергей Сергеевич?'
   answer,events=await core.say(question,timeout=12)
   assert 'Создать контакт?' not in answer,answer
   assert any(e.get('type')=='heard' and e.get('text')==question for e in events),'Original question is swallowed by contact suggestion'
   assert (await core.call('contacts_pending'))['name']=='Сидоров Сергей Сергеевич'
   print('OK: real core FIO suggestion without LLM, draft before creation, explicit confirmation, local save/idempotency/delete; no native address book access')
  finally:await core.stop()

if __name__=='__main__':asyncio.run(main())
