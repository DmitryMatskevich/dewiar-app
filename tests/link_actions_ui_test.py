"""Actual HUD menus: no fetch on paste, explicit actions, source review, failure and narrow widths."""
import os
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui=Path(folder)/'ui';shutil.copytree(ROOT/'ui',ui)
    (ui/'assets/dewiar.js').write_text('''
      window.calls=[];window.opens=[];window.fail=false;
      window.api=()=>({open_settings:(...a)=>opens.push(a)});
      window.core={_on:{},async sendLater(){},on(k,f){(this._on[k]??=[]).push(f);return this},async call(cmd,args={}){
        if(cmd==='link_action'){calls.push({cmd,args});if(fail)throw Error('Fixture: no model');return {id:'fixture-task',status:'running'};}
        if(cmd==='link_choices')return [];return {};}};
      window.fire=(kind,data)=>(core._on[kind]||[]).forEach(fn=>fn(data));
    ''')
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':420,'height':850});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((ui/'hud.html').as_uri());page.evaluate("openAsk();applyLook({skin:'neusky'})")
    field=page.locator('#askin');draft=page.locator('.link-draft-actions')
    field.fill('https://example.org/article')
    assert draft.locator('[data-link-action="seo-page"]').is_visible()
    assert page.evaluate('calls.length')==0,'Pasting must not start work or fetch links'
    assert draft.locator('[data-link-action="audio"]').count()==0
    page.evaluate('sendAsk()');assert draft.locator('article').count()==0,'Sending clears the draft menu, leaving the assistant result'
    field.fill('Проанализируй https://example.org/article');assert draft.locator('article').count()==0
    field.fill('https://youtu.be/test\nhttps://youtu.be/second')
    assert draft.locator('[data-link-action="video"]').is_visible()
    draft.locator('[data-link-action="batch"]').click()
    assert page.locator('#media-panel').is_visible()
    assert page.locator('#mt-links').input_value()=='https://youtu.be/test\nhttps://youtu.be/second'
    assert page.evaluate('calls.length')==0
    page.locator('#mt-close').click()
    assert field.input_value(),'Reviewing and cancelling batch must retain the draft'
    field.fill('https://example.org/article');page.evaluate('fail=true')
    draft.locator('[data-link-action="seo-page"]').click()
    assert 'Fixture: no model' in draft.inner_text()
    assert field.input_value()=='https://example.org/article'
    page.evaluate('fail=false');draft.locator('[data-link-action="seo-site"]').click()
    page.wait_for_function("document.getElementById('askin').value===''")
    assert page.evaluate('calls.at(-1).args.action')=='seo-site'
    # Persisted assistant choice renders via the same shared card.
    page.evaluate("fire('chat',{item:{att:[{kind:'link_actions',urls:['https://example.org/article'],media:false}]}})")
    sent=page.locator('#link-messages .link-actions');assert sent.is_visible()
    sent.locator('[data-link-action="seo-page"]').click()
    sent.locator('[data-link-action="seo-page"]').click()
    assert page.evaluate('calls.filter(x=>x.args.action===\'seo-page\').length')==2,'One failed attempt plus one created task; second click opens task'
    assert page.evaluate('opens.at(-1)')==['tasks','&task=fixture-task']
    assert sent.locator('[data-link-action="seo-site"]').inner_text()=='SEO всего сайта'
    # Unknown media providers can be explicitly classified without fetching.
    sent.locator('.la-mode').click();assert sent.locator('[data-link-action="audio"]').is_visible()
    # A previous screenshot and task result must not compress the assistant menu to zero.
    page.evaluate("document.getElementById('screenshot-messages').innerHTML='<div class=ss-card style=height:160px>Previous screenshot</div>';document.getElementById('task').innerHTML='<div style=height:150px>Completed task</div>';fire('chat',{item:{att:[{kind:'link_actions',urls:['https://example.org/'],media:false}]}})")
    page.wait_for_timeout(100)
    bounds=page.locator('#mid').bounding_box()
    for button in sent.locator('[data-link-action]').all():
        b=button.bounding_box();assert b and b['y']>=bounds['y'] and b['y']+b['height']<=bounds['y']+bounds['height'],(b,bounds)
    for width in (340,420,640):
        page.set_viewport_size({'width':width,'height':850})
        field.fill('https://youtu.be/test')
        for button in draft.locator('[data-link-action]').all():
            b=button.bounding_box();assert b and b['x']>=0 and b['x']+b['width']<=width+1,(width,b)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    shot=ROOT/'work/link-actions-menu.png';shot.parent.mkdir(exist_ok=True);page.screenshot(path=str(shot))
    assert not errors,errors
    browser.close()
print('ALL OK: local detection, media/site choices, batch review, explicit tasks, retry, no duplicate launch, responsive buttons')
