"""Header and content never overlap, including reconnect status and hidden orb."""
import os,shutil,tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as td,sync_playwright() as pw:
 ui=Path(td)/'ui';shutil.copytree(ROOT/'ui',ui)
 (ui/'assets/dewiar.js').write_text('''window.api=()=>({});window.core={connected:true,on(){return this},async call(cmd){if(cmd==='canvas_scope_state')return {mode:'general',messages:[]};return {}},async sendLater(){return {}}};''')
 b=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch();page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto((ui/'hud.html').as_uri());page.wait_for_selector('#assistant-topbar')
 assert page.locator('#assistant-topbar select').count()==0
 assert page.get_by_text('Закрепить холст',exact=True).count()==0
 for width in (300,340,440,640):
  for height in (300,600):
   page.set_viewport_size({'width':width,'height':height})
   for orb in (False,True):
    page.evaluate('''orb=>{applyLook({skin:'neusky'});openAsk();document.querySelector('#card').classList.toggle('no-orb',!orb);health('Микрофон недоступен — переподключаюсь…',true,false,false);document.querySelector('#note').textContent='Микрофон недоступен — переподключаюсь…';}''',orb)
    bounds=page.evaluate('''()=>{let bottom=Math.max(...['#assistant-topbar','#corner-l','#corner-r'].map(s=>document.querySelector(s).getBoundingClientRect().bottom));return {bottom,rows:['#health','#note','#askbox'].map(s=>{let e=document.querySelector(s),r=e.getBoundingClientRect();return {s,top:r.top,visible:r.height>0&&getComputedStyle(e).display!=='none'}})}}''')
    assert all(not r['visible'] or r['top']>=bounds['bottom'] for r in bounds['rows']), (width,height,orb,bounds)
 assert not errors,errors
 page.screenshot(path=str(ROOT/'work/hud-header-fix'/('header-'+os.environ.get('DEWIAR_TEST_BROWSER','chromium')+'.png')))
 b.close();print('PASS: no mode/pin controls; header separate from status, note and input; 300–640px; orb on/off')
