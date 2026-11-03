"""Real access-setup markup + controller; native calls are explicit fixtures, never OS dialogs."""
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
markup=(ROOT/'ui/settings.html').read_text().split('    <div class="prof on" id="mac-access"',1)[1].split('    <div class="prof on sfstop"',1)[0]
markup='<div class="prof on" id="mac-access"'+markup
controller=(ROOT/'ui/studio/mac-permissions.js').read_text()
with tempfile.TemporaryDirectory() as folder,sync_playwright() as pw:
    pagefile=Path(folder)/'access.html'
    pagefile.write_text('''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <style>body{font:16px system-ui;background:#dce7f2;color:#23314a;margin:20px}button{font:inherit;padding:10px;margin:4px;border-radius:12px;border:1px solid #abbcd0;background:#eef5fc}button:disabled{opacity:.5}.hint{font-size:14px;overflow-wrap:anywhere}.actions{display:flex;flex-wrap:wrap}h3{margin-bottom:4px}[hidden]{display:none!important}</style>
      <nav><button data-p="safety">Безопасность</button></nav>'''+markup+'''
      <script>window.nativeCalls=[];window.saved=[];window.S={ui:{}};window.listeners={};
      window.mic={granted:false,state:'not_determined',request_supported:true};window.screen={granted:false,state:'denied',request_supported:true};
      window.files={request_supported:true,pending:'',folders:{workspace:{path:'/fixture/Dewiar',state:'unchecked'},documents:{path:'/fixture/Documents',state:'unchecked'}}};
      window.api=()=>({microphone_permission:async()=>mic,screen_capture_permission:async()=>screen,files_access_state:async()=>files,
        request_microphone_access:async()=>{nativeCalls.push('mic');mic={...mic,pending:true}},
        request_screen_access:async()=>{nativeCalls.push('screen');screen={...screen,pending:true,requested:true}},
        request_files_access:async target=>{nativeCalls.push('files:'+target);files.pending=target},
        open_microphone_settings:async()=>nativeCalls.push('settings:mic'),open_screen_settings:async()=>nativeCalls.push('settings:screen'),open_files_settings:async()=>nativeCalls.push('settings:files')});
      window.core={on(k,f){listeners[k]=f;return this},async call(cmd,args){saved.push(args.patch);S={ui:{...S.ui,...args.patch.ui}};return S}};
      </script><script>'''+controller+'</script>')
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':900,'height':1050});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(pagefile.as_uri()+'?mac_access_setup=1')
    page.wait_for_selector('#mac-access-setup:visible')
    assert page.evaluate('nativeCalls')==[], 'Startup must not trigger protected requests'
    assert page.evaluate('saved')==[{'ui':{'mac_access_setup_seen':True}}]
    assert 'Ещё не проверено' in page.locator('#mac-access-files').inner_text()
    page.locator('#mac-access-screen-allow').click()
    assert page.evaluate('nativeCalls')==['screen']
    assert page.locator('#mac-access-screen-allow').is_disabled()
    page.evaluate("screen={...screen,pending:false,granted:true};window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.getElementById('mac-access-screen').textContent.includes('разрешён')")
    page.locator('[data-mac-folder="documents"]').click()
    assert page.evaluate('files.pending')=='documents'
    assert page.locator('[data-mac-folder="workspace"]').is_disabled()
    page.evaluate("files.pending='';files.folders.documents={path:'/fixture/Documents',state:'denied',error:'Нет доступа'};window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.getElementById('mac-access-files').textContent.includes('Нет доступа')")
    page.locator('#mac-access-files-settings').click()
    assert 'settings:files' in page.evaluate('nativeCalls')
    page.locator('#mac-access-startup').check()
    page.wait_for_function('S.ui.mac_access_at_startup===true')
    assert page.locator('#mac-access-files-startup').is_checked()
    page.locator('#mac-access-files-startup').uncheck()
    page.wait_for_function('S.ui.mac_files_request_at_startup===false')
    for target in ['desktop','downloads']:
        page.locator(f'[data-mac-folder="{target}"]').click()
        assert page.evaluate('files.pending')==target
        page.evaluate("files.pending='';window.dispatchEvent(new Event('focus'))")
        page.wait_for_function("!document.querySelector('[data-mac-folder=workspace]').disabled")
    page.locator('#mac-access-later').click();assert page.locator('#mac-access-setup').is_hidden()
    assert page.locator('#mac-access').is_visible()
    page.evaluate("mic={...mic,granted:true};files.folders.workspace={path:'/fixture/Dewiar',state:'authorized',readable:true};window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.getElementById('mac-access-files').textContent.includes('Чтение разрешено')")
    assert page.locator('#mac-access-mic-allow').is_disabled()
    shot=ROOT/'work/mac-access/setup.png';shot.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(shot),full_page=True)
    page.evaluate("screen.request_supported=false;files.request_supported=false;window.dispatchEvent(new Event('focus'))")
    page.wait_for_selector('#mac-access-source:visible')
    assert page.locator('[data-mac-folder="selected"]').is_disabled()
    page.set_viewport_size({'width':640,'height':900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors,errors
    # Verify the same card in the full Studio DOM and its actual theme/layout.
    full=browser.new_page(viewport={'width':1200,'height':850})
    full.add_init_script("""window.pywebview={api:{}};const wait=()=>{if(!window.core)return setTimeout(wait,5);core.retry=()=>{};core.connect=()=>{};if(core.ws){core.ws.onclose=null;core.ws.close();}core.call=async()=>[];};wait();""")
    full.goto((ROOT/'ui/settings.html').as_uri()+'?port=65534')
    full.wait_for_function("typeof showHelp==='function'")
    full.evaluate("""()=>{
      S={ui:{},privacy:{},llm:{profiles:{}}};
      window.api=()=>({microphone_permission:async()=>({granted:true,state:'authorized'}),screen_capture_permission:async()=>({granted:true,state:'authorized',request_supported:true}),files_access_state:async()=>({folders:{workspace:{state:'unchecked',path:'/fixture/Dewiar'},documents:{state:'unchecked',path:'/fixture/Documents'}}})});
      document.querySelectorAll('main section').forEach(s=>s.classList.toggle('cur',s.id==='p-safety'));
      window.dispatchEvent(new Event('focus'));
    }""")
    full.wait_for_selector('#mac-access:visible')
    assert full.locator('#p-safety [data-section-help="safety"]').is_visible()
    card=full.locator('#mac-access').bounding_box()
    for button in full.locator('#mac-access button').all():
        if button.is_visible():
            rect=button.bounding_box()
            assert rect['x']>=card['x'] and rect['x']+rect['width']<=card['x']+card['width']+1
    full.screenshot(path=str(shot.parent/'studio.png'),full_page=True)
    full.evaluate("showHelp('safety')")
    assert 'Доступы macOS' in full.locator('#help-body').inner_text()
    browser.close()
print('ALL OK: startup without automatic prompts, explicit screen/documents requests, pending state, settings, preference, source guard and layout')
