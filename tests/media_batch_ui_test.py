"""Real transcription dialog: batch review, existing uploads, one launch and task navigation."""
import shutil,tempfile,sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder,sync_playwright() as p:
 ui=Path(folder)/'ui';shutil.copytree(ROOT/'ui',ui)
 (ui/'assets/dewiar.js').write_text('''window.__calls=[];window.__opens=[];window.api=()=>({pick_media_files:async()=>['/Downloads/lecture.mp4','/Downloads/meeting.wav'],open_settings:(...a)=>__opens.push(a)});window.core={_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(cmd,args){if(cmd==='media_transcribe'){__calls.push(args);return await new Promise(r=>window.__release=()=>r({id:'abcd1234',status:'running',title:'Извлечение текста',media_sources:args.sources}));}return {}}};''')
 browser=p.chromium.launch();page=browser.new_page(viewport={'width':420,'height':740});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto((ui/'hud.html').as_uri());page.wait_for_timeout(300)
 page.evaluate("openAsk(); atts=[{kind:'file',name:'already-selected.wav',b64:'YXVkaW8=',size:5}];renderAtts()")
 assert page.locator('[data-transcribe-open]').count()==0, 'Transcription must not add a HUD toolbar button'
 page.evaluate('openTranscription()');dialog=page.locator('#media-panel');assert dialog.is_visible()
 assert 'Повторное' in page.locator('#mt-start').get_attribute('title')
 assert 'already-selected.wav' in page.locator('#mt-files').inner_text()
 page.locator('#mt-links').fill('https://youtu.be/first\nhttps://youtube.com/watch?v=first&t=10\nhttps://youtu.be/second')
 assert '3 источников' in page.locator('#mt-count').inner_text()
 page.locator('#mt-pick').click();assert '5 источников' in page.locator('#mt-count').inner_text()
 shot=ROOT/'work/media-batch/dialog.png';shot.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(shot))
 page.locator('#mt-start').click();assert page.locator('#mt-start').is_disabled()
 assert len(page.evaluate('__calls'))==1
 call=page.evaluate('__calls[0]');assert len(call['sources'])==4 and call['files'][0]['name']=='already-selected.wav',call
 page.evaluate('__release()');page.wait_for_timeout(100)
 assert not dialog.is_visible();assert page.evaluate('__opens')==[['tasks','&task=abcd1234']]
 assert page.evaluate('atts.length')==0
 assert not errors,errors
 browser.close()
print('ALL OK: no HUD transcription button; dialog, detailed title, batch review, duplicate URLs removed, existing attachment reused, multiple files, single request, task opened')
