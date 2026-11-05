"""HUD mail badge, unread navigation, help popover and quiet startup behavior."""
import os
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui = Path(folder) / "ui"
    shutil.copytree(ROOT / "ui", ui)
    (ui / "assets/dewiar.js").write_text('''window.calls=[];window.opens=[];
      window.api=()=>({open_settings:(...args)=>opens.push(args)});
      window.core={_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(cmd,args={}){calls.push([cmd,args]);if(cmd==='mail_badge_counts')return {badge:3,filters:{important:true,unanswered:true}};return {};}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));''')
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page(viewport={"width": 420, "height": 760})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto((ui / "hud.html").as_uri())
    assert page.locator("#link-messages .link-actions").count() == 0
    assert page.locator("#b-mail-count").is_hidden()
    page.evaluate("refreshMailCounts()")
    page.wait_for_function("document.getElementById('b-mail-count').textContent==='3'")
    button = page.locator("#b-mail")
    assert button.evaluate("e=>e.classList.contains('has-mail-count')")
    assert button.get_attribute("aria-label") == "Писем важные, без ответа: 3. Открыть список"
    button.click(force=True)
    assert page.evaluate("opens.at(-1)") == ["mail", "&badge=1"]
    page.evaluate("fire('mail_counts',{badge:0,filters:{important:true,unanswered:true},attention:1})")
    assert page.locator("#b-mail-count").text_content() == "!"
    assert "ждёт пароль" in button.get_attribute("aria-label")
    button.click(force=True)
    assert page.evaluate("opens.at(-1)") == ["mail", "&attention=1"]
    page.evaluate("fire('mail_counts',{badge:0,filters:{important:true,unanswered:true},attention:0})")
    assert page.locator("#b-mail-count").is_hidden()
    help_button = page.locator("#b-help")
    hide_button = page.locator("#b-tray")
    assert page.evaluate("e=>e.parentElement===document.getElementById('corner-r')", hide_button.element_handle())
    assert page.evaluate("document.getElementById('b-help').compareDocumentPosition(document.getElementById('b-tray')) & Node.DOCUMENT_POSITION_PRECEDING")
    help_button.click(force=True)
    assert page.locator("#hud-help").is_visible()
    assert "Ссылки и файлы" in page.locator("#hud-help").inner_text()
    page.keyboard.press("Escape")
    assert page.locator("#hud-help").is_hidden()
    for width in (340, 420, 640):
        page.set_viewport_size({"width": width, "height": 760})
        button_box = button.bounding_box()
        assert button_box and button_box["x"] < width / 2
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    assert not errors, errors
    browser.close()
print("ALL OK: configured mail badge, badge navigation, help placement and controls, no stale startup card, responsive HUD")
