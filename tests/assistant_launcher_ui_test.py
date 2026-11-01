"""Real Studio launcher DOM: name, sleep, wake/recreate, keyboard, failures and Help."""
import re
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work" / "assistant-launcher"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route(re.compile(r"^https?://"), lambda route: route.abort())
    page.add_init_script("""
      window.launcherState = {loaded:false, visible:false, in_tray:true};
      window.launcherCalls = []; window.launcherDelay = false; window.launcherFail = false;
      window.launcherHideDelay = false; window.launcherStateDelay = false;
      window.pywebview = {api:{
        tray_state: async () => {
          const state={...window.launcherState};
          if(launcherStateDelay) await new Promise(resolve=>window.releaseState=resolve);
          return state;
        },
        show: async () => {launcherCalls.push('show');launcherState={loaded:true,visible:true,in_tray:false};},
        hide: async () => {launcherCalls.push('hide');
          if(launcherHideDelay) await new Promise(resolve=>window.releaseHide=resolve);
          launcherState.visible=false;launcherState.in_tray=true;},
        wake_agent: async () => {launcherCalls.push('wake');
          if(launcherDelay) await new Promise(resolve=>window.releaseWake=resolve);
          if(launcherFail) throw new Error('Не удалось загрузить: тестовая ошибка');
          launcherState={loaded:true,visible:true,in_tray:false};
          return {ok:true,...launcherState};}
      }};
      const wait=()=>{if(!window.core)return setTimeout(wait,5);
        core.retry=()=>{};core.connect=()=>{};
        if(core.ws){core.ws.onclose=null;core.ws.close();}
        core.call=async()=>[];
      };wait();
    """)
    page.goto((ROOT / "ui/settings.html").as_uri() + "?port=65534")
    page.wait_for_function("typeof renderPersonas === 'function' && typeof showHelp === 'function'")
    avatar = (ROOT / "data/avatars/lira.webp").as_uri()
    page.evaluate("""avatar => {
      core.connected=true;agentCoreOnline=true;
      loadGallery=async()=>{};
      personas=[{id:'lira',name:'Лира',gender:'female',color:'#9BD4FF',style:'photo',live:'lira',avatar_url:avatar}];
      active='lira';renderPersonas();studioSkin('neusky');
    }""", avatar)
    button = page.locator("#agent-toggle")
    assert button.inner_text() == "Лира"
    assert page.locator("#bsub").count() == 0
    assert button.get_attribute("aria-pressed") == "false"
    assert "спит" in button.get_attribute("title")
    page.wait_for_timeout(1600)
    button.screenshot(path=str(OUT / "sleeping.png"))

    button.click()
    page.wait_for_function("document.querySelector('#agent-toggle').getAttribute('aria-pressed') === 'true'")
    assert page.evaluate("launcherCalls") == ["wake"]
    button.screenshot(path=str(OUT / "awake.png"))
    # A pending status poll must neither delay hiding nor restore an old awake state.
    page.evaluate("launcherStateDelay=true;void refreshAgentWindowState()")
    page.wait_for_function("!!window.releaseState")
    page.evaluate("launcherStateDelay=false;launcherHideDelay=true")
    button.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("!!window.releaseHide")
    assert button.get_attribute("aria-pressed") == "false"
    assert "скрываю" in button.get_attribute("title")
    assert page.evaluate("launcherCalls") == ["wake", "hide"]
    page.evaluate("releaseHide();launcherHideDelay=false")
    page.wait_for_function("document.querySelector('#agent-toggle').getAttribute('aria-busy') === 'false'")
    page.evaluate("releaseState()")
    page.wait_for_function("document.querySelector('#agent-toggle').classList.contains('agent-sleeping')")
    assert page.evaluate("launcherCalls") == ["wake", "hide"]

    # Native close after an external reopen: next click must recreate, not hide a stale window.
    page.evaluate("launcherState={loaded:false,visible:false,in_tray:true}")
    page.keyboard.press("Space")
    page.wait_for_function("document.querySelector('#agent-toggle').getAttribute('aria-pressed') === 'true'")
    assert page.evaluate("launcherCalls.at(-1)") == "wake"

    # Poll reflects native hide/close without requiring a click inside Studio.
    page.evaluate("launcherState={loaded:false,visible:false,in_tray:true}")
    page.wait_for_function("document.querySelector('#agent-toggle').getAttribute('aria-pressed') === 'false'")
    page.evaluate("launcherDelay=true")
    button.click()
    page.wait_for_function("!!window.releaseWake")
    assert button.get_attribute("aria-busy") == "true"
    before = page.evaluate("launcherCalls.length")
    assert button.get_attribute("aria-disabled") == "true"
    button.dispatch_event("click")  # synthetic repeated input must also be rejected by the handler
    assert page.evaluate("launcherCalls.length") == before
    page.evaluate("releaseWake();launcherDelay=false")
    page.wait_for_function("document.querySelector('#agent-toggle').getAttribute('aria-busy') === 'false'")

    # Disconnection sleeps the portrait even when the window exists.
    page.evaluate("core.connected=false;core.emit('offline',{})")
    assert "agent-sleeping" in button.get_attribute("class")
    button.click()
    page.wait_for_function("document.querySelector('#agent-toggle').getAttribute('aria-busy') === 'false'")
    assert page.evaluate("launcherCalls.at(-1)") == "hide", "an open offline window must hide in one click"
    page.evaluate("launcherFail=true")
    button.click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('тестовая ошибка')")
    assert button.get_attribute("aria-busy") == "false"
    assert "agent-sleeping" in button.get_attribute("class")
    assert page.evaluate("curName()") == "Лира", "diary name must not depend on removed subtitle"

    # Another persona updates the label safely, independent of layout.
    page.evaluate("personas[0].name='<Лира & помощник>';renderPersonas()")
    assert button.inner_text() == "<Лира & помощник>"
    assert button.locator("b img,b script").count() == 0
    page.evaluate("showHelp('home')")
    assert "портрет спит" in page.locator("#help-body").inner_text()
    assert "не означает отключение микрофона" in page.locator("#help-body").inner_text()
    for key in page.evaluate("Object.keys(DEWIAR_HELP)"):
        page.evaluate("key=>showHelp(key)", key)
        assert page.locator("#help-body").inner_text(), key
    assert not errors, errors
    browser.close()
print("OK: assistant launcher sleep/wake, native close, keyboard, duplicate prevention, errors, persona name and Help")
