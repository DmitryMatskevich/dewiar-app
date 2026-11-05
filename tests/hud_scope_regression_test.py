"""HUD controls never drag; stale audio state requests native permission first. No real audio."""
import os,shutil,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as td,sync_playwright() as pw:
 ui=Path(td)/'ui';shutil.copytree(ROOT/'ui',ui)
 (ui/'assets/dewiar.js').write_text('''
 window.calls=[];window.moves=[];window.permission='not_determined';window.scopeMode='general';
 window.pywebview={api:{async microphone_permission(){return {state:permission,granted:permission==='authorized'}},async request_microphone_access(){calls.push('ALLOW');permission='authorized';return {state:permission,granted:true}},async open_microphone_settings(){calls.push('SETTINGS')},async get_pos(){return [100,100]},async move_to(...a){moves.push(a)}}};window.api=()=>pywebview.api;
 window.core={connected:true,on(){return this},async call(cmd,a={}){calls.push(cmd);if(cmd==='audio_state')return {audio_mode:'text'};if(cmd==='canvas_scope_mode')scopeMode=a.mode;if(cmd.startsWith('canvas_scope_'))return {mode:scopeMode,canvas_id:null,messages:[]};if(cmd==='dictation_start')return {session:1};return {}},async sendLater(){return {}}};
 ''')
 browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch();page=browser.new_page(viewport={'width':640,'height':850});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto((ui/'hud.html').as_uri());page.evaluate("openAsk();applyLook({skin:'neusky'});showCap('','')")
 select=page.get_by_label('Область работы Лиры');select.select_option('follow');page.mouse.move(400,450)
 assert page.evaluate('drag===null&&moves.length===0')
 assert page.locator('#assistant-topbar #b-voice-inline').count()==0
 assert page.locator('.ask-actions #b-voice-inline').count()==1
 for width in (340,440,640):
  page.set_viewport_size({'width':width,'height':850})
  assert page.evaluate("(()=>{const a=document.querySelector('#assistant-topbar').getBoundingClientRect();return a.left>=0&&a.right<=innerWidth})()")
 # Pretend the UI still says voice while the core is in text mode.
 page.evaluate("updateAudioMode('voice')")
 page.locator('#a-mic').click();page.wait_for_function("calls.includes('dictation_start')")
 assert page.evaluate("calls.indexOf('ALLOW')<calls.indexOf('audio_enable')&&calls.indexOf('audio_enable')<calls.indexOf('dictation_start')")
 page.evaluate("dictationUi(false);permission='denied';updateAudioMode('text');calls=[]")
 page.locator('#a-mic').click();page.wait_for_function("calls.includes('SETTINGS')")
 assert not page.evaluate("calls.includes('dictation_start')")
 assert not errors,errors
 (ROOT/'work/hud-scope-fix').mkdir(exist_ok=True);page.screenshot(path=str(ROOT/'work/hud-scope-fix'/('hud-'+os.environ.get('DEWIAR_TEST_BROWSER','chromium')+'.png')))
 browser.close();print('PASS: mode select no pointer capture/window movement; voice control stays left of microphone; stale voice state Allow before activation; denied opens settings')
