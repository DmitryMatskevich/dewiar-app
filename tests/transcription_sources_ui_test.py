"""Real shared dialog: explicit native paste, browser selection, append/dedup, failures, layout."""
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as p:
    ui = Path(folder)/'ui'; shutil.copytree(ROOT/'ui',ui)
    (ui/'assets/dewiar.js').write_text('''
window.__clip='';window.__browserCalls=[];window.__browserResult={url:'https://youtu.be/from-tab'};
window.__browsers=[{id:'safari',name:'Safari'},{id:'yandex',name:'Яндекс Браузер'}];
window.api=()=>({clipboard_text:async()=>__clip,transcription_browsers:async()=>__browsers,
transcription_browser_url:async id=>{__browserCalls.push(id);return __browserResult}});
window.core={on(){return this},async call(){return {}}};
''')
    browser=p.chromium.launch();page=browser.new_page(viewport={'width':420,'height':740})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((ui/'hud.html').as_uri());page.evaluate('openTranscription()')
    page.wait_for_function("document.querySelector('#mt-browser').options.length===2")
    assert page.evaluate('__browserCalls.length') == 0, 'Opening dialog must not read tabs'
    page.locator('#mt-paste').click()
    assert 'нет текста' in page.locator('#mt-error').inner_text()
    assert page.locator('#mt-start').is_disabled()
    page.evaluate("__clip='https://youtu.be/clipboard\\n/Downloads/lecture.wav'")
    page.locator('#mt-paste').click()
    assert page.locator('#mt-links').input_value() == 'https://youtu.be/clipboard\n/Downloads/lecture.wav'
    assert page.locator('#mt-start').is_enabled()
    page.locator('#mt-paste').click()
    assert 'уже добавлены' in page.locator('#mt-import-status').inner_text()
    page.locator('#mt-browser').select_option('yandex');page.locator('#mt-from-browser').click()
    assert page.evaluate('__browserCalls') == ['yandex']
    value=page.locator('#mt-links').input_value();assert value.endswith('\nhttps://youtu.be/from-tab')
    page.locator('#mt-from-browser').click();assert page.locator('#mt-links').input_value()==value
    page.evaluate("__browserResult={error:'Разрешите доступ в Автоматизация'}")
    page.locator('#mt-from-browser').click()
    assert 'Автоматизация' in page.locator('#mt-error').inner_text()
    assert page.locator('#mt-links').input_value()==value
    # Pending native request blocks a premature start without replacing existing input.
    page.evaluate("window.api=()=>({clipboard_text:()=>new Promise(r=>window.__resolvePaste=r)})")
    page.locator('#mt-paste').click();assert page.locator('#mt-start').is_disabled()
    page.evaluate("__resolvePaste('https://youtu.be/late')")
    page.wait_for_function("!document.querySelector('#mt-paste').disabled")
    assert page.locator('#mt-links').input_value().endswith('\nhttps://youtu.be/late')
    # Web fallback still accepts clipboard text; failure points to ordinary keyboard paste.
    page.evaluate("window.api=()=>null;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:async()=> 'https://youtu.be/web'}})")
    page.locator('#mt-paste').click();assert page.locator('#mt-links').input_value().endswith('\nhttps://youtu.be/web')
    page.evaluate("() => { navigator.clipboard.readText=async()=>{throw Error('denied')}; }")
    page.locator('#mt-paste').click();assert 'Cmd+V' in page.locator('#mt-error').inner_text()
    page.locator('#mt-refresh-browsers').click()
    assert page.locator('#mt-from-browser').is_disabled()
    assert page.locator('#mt-browser').is_disabled()
    page.evaluate("window.api=()=>({transcription_browsers:async()=>[{id:'safari',name:'Safari'},{id:'yandex',name:'Яндекс Браузер'}]})")
    page.locator('#mt-refresh-browsers').click()
    page.wait_for_function("document.querySelector('#mt-browser').options.length===2")
    page.locator('#mt-error').evaluate("e=>e.textContent=''")
    out=ROOT/'work/media-batch';out.mkdir(parents=True,exist_ok=True)
    for width,theme in [(420,''),(320,'light'),(1100,'light')]:
        page.set_viewport_size({'width':width,'height':850});page.evaluate('(theme)=>document.body.classList.toggle("light",theme==="light")',theme)
        assert page.locator('#media-panel').evaluate('e=>e.scrollWidth<=e.clientWidth'), 'Dialog must fit narrow viewport'
        page.screenshot(path=str(out/f'sources-{width}.png'))
    assert not errors,errors
    browser.close()
print('ALL OK: clipboard native/web, empty/denied/busy states, browser choice/refresh, append/dedup, no automatic tab read, narrow/light layouts')
