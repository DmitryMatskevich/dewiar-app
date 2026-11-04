"""Real HUD routing with simulated native permissions; no OS grants or recordings."""
import os
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui = Path(folder) / 'ui'
    shutil.copytree(ROOT / 'ui', ui)
    (ui / 'assets/dewiar.js').write_text('''
      window.opens=[];window.nativeCalls=[];window.commands=[];window.screenGranted=false;
      window.folders={request_supported:true,pending:'',folders:{documents:{state:'unchecked',readable:false},workspace:{state:'unchecked',readable:false}}};
      window.api=()=>({
        async screen_capture_permission(){return {granted:screenGranted}},
        async request_screen_access(){nativeCalls.push('screen')},
        async files_access_state(){if(window.failFiles)throw Error('Offline');return structuredClone(folders)},
        async request_files_access(target){nativeCalls.push('files:'+target);folders.pending='features';return structuredClone(folders)},
        async open_settings(...args){opens.push(args)},async open_prompter(){opens.push(['prompter'])}
      });
      window.core={snapshot:{},_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(cmd,args={}){
        commands.push([cmd,args]);if(cmd==='prompter_capture_apps')return {apps:[{name:'Fixture',title:'Demo',key:'demo'}]};return {};}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));
      window.resolveFiles=state=>{folders.pending='';for(const key of ['documents','workspace'])folders.folders[key]={state,readable:state==='authorized'};};
    ''')
    browser = getattr(pw, os.environ.get('DEWIAR_TEST_BROWSER', 'chromium')).launch()
    page = browser.new_page(viewport={'width':440,'height':900})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto((ui/'hud.html').as_uri())
    page.evaluate("openAsk();applyLook({skin:'neusky'});showCap('','')")
    assert page.evaluate("['a-meet','a-shot','b-mail','b-cal'].every(id=>document.getElementById(id).parentElement.id==='bar')")
    for width in (260, 340, 440, 640):
        page.set_viewport_size({'width':width,'height':900})
        bounds = page.evaluate("""() => {
          const ids=['a-meet','a-shot','b-mail','b-cal','b-notes','b-chat','b-set'];
          return ids.map(id=>{let r=document.getElementById(id).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}});
        }""")
        assert bounds[0]['left'] >= 0 and bounds[-1]['right'] <= width, (width,bounds)
        assert all(a['right'] <= b['left'] and a['top']==b['top'] for a,b in zip(bounds,bounds[1:])), (width,bounds)
    page.set_viewport_size({'width':440,'height':900})
    for id in ['a-shot','a-meet','b-cal','b-notes']:
        page.wait_for_function('(id)=>document.getElementById(id).dataset.access === "unavailable"',arg=id)
        assert float(page.locator('#'+id+' svg').evaluate('e=>getComputedStyle(e).opacity')) < .5
    page.locator('#a-meet').click();page.wait_for_function("nativeCalls.includes('screen')")
    assert page.locator('#quick-meeting-dialog').count()==0
    assert not page.evaluate("commands.some(c=>c[0]==='prompter_start')")
    # Notifications must not hide the permission guidance or restore an active badge.
    page.evaluate("markNewNote({kind:'note',path:'Заметки/fixture.md',title:'Fixture'})")
    assert 'доступ' in page.locator('#b-notes').get_attribute('aria-label')
    assert not page.locator('#b-notes-dot').is_visible()
    page.locator('#b-cal').click();page.wait_for_function("nativeCalls.includes('files:features')")
    assert page.evaluate('opens')==[]
    page.locator('#b-notes').click()
    assert page.evaluate("nativeCalls.filter(c=>c==='files:features').length")==1
    page.evaluate("resolveFiles('denied')")
    page.wait_for_function("!document.getElementById('b-cal').dataset.busy")
    assert page.evaluate('opens')==[]
    page.locator('#b-notes').click()
    page.wait_for_function("nativeCalls.filter(c=>c==='files:features').length===2")
    page.evaluate("resolveFiles('authorized')")
    page.wait_for_function("opens.some(o=>o[0]==='notes')")
    assert page.evaluate('opens')==[['notes','&note=%D0%97%D0%B0%D0%BC%D0%B5%D1%82%D0%BA%D0%B8%2Ffixture.md']]
    assert page.locator('#b-notes-dot').is_visible()
    # A revoked grant changes both buttons on focus and errors fail closed.
    page.evaluate("resolveFiles('denied');window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.getElementById('b-cal').dataset.access==='unavailable'")
    page.evaluate("failFiles=true;window.dispatchEvent(new Event('focus'))")
    page.locator('#b-cal').click()
    page.wait_for_function("nativeCalls.filter(c=>c==='files:features').length===3")
    page.evaluate("resolveFiles('denied')")
    page.wait_for_function("!document.getElementById('b-cal').dataset.busy")
    assert len(page.evaluate('opens'))==1
    page.evaluate("failFiles=false;screenGranted=true;window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.getElementById('a-meet').dataset.access==='available'")
    page.locator('#a-meet').click();page.locator('#quick-meeting-dialog').wait_for()
    assert not page.locator('#qm-app-wrap').is_visible()
    page.locator('[data-source=application]').click()
    page.locator('#qm-app').select_option('demo');page.locator('#qm-consent').check()
    page.locator('#qm-start').click();page.wait_for_function("opens.some(o=>o[0]==='prompter')")
    args=page.evaluate("commands.find(c=>c[0]==='prompter_start')[1]")
    assert args['capture_application']=='demo' and args['auto_interval']==.5 and args['frame_quality']==100
    page.evaluate("screenGranted=false")
    page.locator('#a-meet').click()
    page.wait_for_function("document.getElementById('a-meet').dataset.access==='unavailable'")
    assert not page.locator('#quick-meeting-dialog').is_visible()
    assert not errors,errors
    browser.close()
print('OK: permission-gated meeting/calendar/notes, Allow/denial/revocation, shared requests, badges, working meeting selector')
