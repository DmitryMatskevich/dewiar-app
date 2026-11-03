"""Meeting controls recover from denied screen access without restarting the page."""
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
markup = (ROOT/'ui/settings.html').read_text().split('<section id="p-prompter">', 1)[1].split('</section>', 1)[0]
script = (ROOT/'ui/studio/prompter.js').read_text()
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    file = Path(folder)/'capture.html'
    file.write_text('''<meta charset="utf-8"><style>[hidden],.hidden{display:none!important}</style>'''+markup+'''
<script>
const $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s), get=()=>null, escH=s=>s;
window.refreshStatic=()=>{};window.calls=[];window.listeners={};window.nativeCalls=[];
window.screenState={granted:false,pending:false,requested:false,request_supported:true};
window.api=()=>({screen_capture_permission:async()=>screenState,request_screen_access:async()=>{nativeCalls.push('request');screenState={...screenState,pending:true,requested:true}},open_screen_settings:async()=>nativeCalls.push('settings')});
window.core={on:(n,f)=>listeners[n]=f,call:async(n,a)=>{calls.push(n);return n==='prompter_schedules'?[]:n==='prompter_capture_apps'?{supported:true,apps:[{key:'Meeting notes',name:'Notes',title:'Meeting notes'}]}:n==='prompter_capture_test'?{width:640,height:480}:{};}};
</script><script>'''+script+'</script>')
    browser = pw.chromium.launch()
    page = browser.new_page(); errors=[]
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(file.as_uri()); page.evaluate("listeners.hello()")
    page.wait_for_selector('#pr-screen-access:visible')
    assert page.locator('#pr-capture-test').is_disabled()
    assert 'prompter_capture_apps' not in page.evaluate('calls')
    page.locator('#pr-screen-allow').click()
    page.wait_for_function('screenState.pending')
    assert page.evaluate('nativeCalls') == ['request']
    page.evaluate("screenState={...screenState,pending:false,granted:true};dispatchEvent(new Event('focus'))")
    page.wait_for_function("document.querySelector('#pr-capture-application').dataset.loaded==='1'")
    assert page.locator('#pr-screen-access').is_hidden()
    assert page.locator('#pr-capture-test').is_enabled()
    assert 'Notes — Meeting notes' in page.locator('#pr-capture-application').text_content()
    page.locator('#pr-capture-test').click()
    page.wait_for_function("document.querySelector('#pr-capture-test-result').textContent.includes('640 × 480')")
    assert not errors, errors
    browser.close()
print('OK: denied access, native request, grant recovery, window list and diagnostic')
