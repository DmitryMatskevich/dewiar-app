"""Actual HUD UI: persistent top-row task state, animation, deep link, reduced motion."""
import shutil,tempfile,sys,os
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as p:
 ui=Path(folder)/'ui';shutil.copytree(ROOT/'ui',ui)
 (ui/'assets/dewiar.js').write_text('''window.__opens=[];window.api=()=>({open_settings:(...a)=>__opens.push(a)});window.core={_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(){return {}}};''')
 browser=getattr(p,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
 page=browser.new_page(viewport={'width':340,'height':500})
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto((ui/'hud.html').as_uri());page.wait_for_timeout(300)
 assert page.locator('#b-tasks').is_hidden()
 assert page.locator('#corner-l > button').evaluate_all('(buttons)=>buttons.map(b=>b.id)')==['b-lo','b-bug','b-upd','b-tasks']
 assert page.locator('[data-transcribe-open]').count()==0
 assert page.locator('#b-tasks-count').is_hidden()
 page.evaluate('''() => { tasks.abc12345={id:'abc12345',status:'running',title:'Извлечение текста',activity:'Распознано 1:00 из 10:00',steps:1};renderTask(); }''')
 button=page.locator('#b-tasks');assert button.is_visible()
 assert button.get_attribute('class').split().count('working')==1
 assert '1:00 из 10:00' in button.get_attribute('title')
 assert page.locator('#b-tasks-count').inner_text()=='1'
 assert page.locator('#b-tasks-count').is_visible()
 page.evaluate("showCap('Давай сделаем транскрибацию ролика', 'Задача запущена. Ход обработки — по кнопке задач в верхней строке.')")
 page.wait_for_timeout(250)
 shot=ROOT/'work/media-routing/task-indicator.png';shot.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(shot))
 assert page.evaluate("getComputedStyle(document.getElementById('b-tasks')).animationName")=='taskPulse'
 rect=button.bounding_box();assert rect['y']<45 and rect['x']>=0
 update=page.locator('#b-upd').bounding_box();badge=page.locator('#b-tasks-count').bounding_box()
 assert rect['x']>=update['x']+update['width'],(update,rect)
 assert badge['y']>=rect['y'] and badge['y']+badge['height']<=rect['y']+rect['height'],(rect,badge)
 button.click(force=True);assert page.evaluate('__opens')==[['tasks','&task=abc12345']]
 page.emulate_media(reduced_motion='reduce')
 assert page.evaluate("getComputedStyle(document.getElementById('b-tasks')).animationName")=='none'
 page.evaluate("tasks.abc12345.status='done';renderTask()")
 assert 'working' not in button.get_attribute('class') and button.is_visible()
 assert page.locator('#b-tasks-count').is_hidden(), 'Completed tasks must not leave an empty colored dash'
 for width in [280,340,500,680]:
  page.set_viewport_size({'width':width,'height':650})
  left=page.locator('#corner-l').bounding_box();right=page.locator('#corner').bounding_box()
  assert left['x']+left['width']<=right['x'],(width,left,right)
  assert page.locator('#corner-l button').evaluate_all('(buttons)=>buttons.every(b=>b.textContent.trim()==="")')
 page.set_viewport_size({'width':420,'height':650})
 page.evaluate("applyLook({skin:'neusky'})")
 page.screenshot(path=str(shot.parent/'toolbar-completed.png'))
 page.evaluate("for(const f of core._on.tasks)f({tasks:[]})")
 assert button.is_hidden()
 assert not errors,errors
 browser.close()
print('ALL OK: tasks after refresh, no transcription button, no empty badge/dash, count stays inside row, responsive layout, title, deep link, completion, reduced motion, removal')
