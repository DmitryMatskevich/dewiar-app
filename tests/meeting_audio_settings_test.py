"""Real settings DOM: audio app discovery, persisted selection and literal labels."""
import re
import sys
import copy
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from settings import DEFAULTS
fixture = copy.deepcopy(DEFAULTS)
fixture["prompter"].update(tap_target="us.zoom.xos", system_audio="tap", record_me=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route(re.compile("^https?://"), lambda route: route.abort())
    page.add_init_script("""window.pywebview={api:{}};
      const wait=()=>{if(!window.core)return setTimeout(wait,5);core.retry=()=>{};core.connect=()=>{};
        if(core.ws){core.ws.onclose=null;core.ws.close();}core.call=async()=>({});};wait();""")
    page.goto((ROOT / "ui/settings.html").as_uri() + "?port=1")
    page.wait_for_function("typeof refreshStatic === 'function'")
    page.evaluate("""settings => {
      S=settings;
      refreshStatic();
      document.querySelectorAll('main section').forEach(s=>s.classList.toggle('cur',s.id==='p-prompter'));
      core.call=async cmd=>cmd==='prompter_audio_apps'?{supported:true,apps:[{bundle:'us.zoom.xos',name:'Zoom',playing:true},{bundle:'test.app',name:'<img src=x onerror=alert(1)>',playing:false}]}:{};
    }""", fixture)
    page.locator("#pr-audio-apps-refresh").click()
    page.wait_for_function("document.querySelector('#pr-audio-apps-result').textContent.includes('приложений: 2')")
    assert page.locator("#pr-tap-target").input_value() == "us.zoom.xos"
    assert page.locator("#pr-tap-target option").count() == 3
    assert page.locator("#pr-tap-target img").count() == 0
    assert page.locator("#pr-system-audio").input_value() == "tap"
    assert page.locator('[data-k="prompter.record_me"]').is_checked()
    page.locator("#pr-tap-target").select_option("test.app")
    page.locator("#pr-audio-apps-refresh").click()
    page.wait_for_function("!document.querySelector('#pr-audio-apps-refresh').disabled")
    assert page.locator("#pr-tap-target").input_value() == "test.app", "refresh lost an unsaved choice"
    assert not errors, errors
    browser.close()
print("OK: meeting capture settings, app discovery, preserved choice and escaped labels")
