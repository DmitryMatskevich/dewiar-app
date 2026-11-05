"""Health notices reserve their own row below the optional HUD top bar."""
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as td, sync_playwright() as pw:
    ui = Path(td) / "ui"
    shutil.copytree(ROOT / "ui", ui)
    (ui / "assets/dewiar.js").write_text(
        "window.api=()=>null;window.core={_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(){return {}}};",
        encoding="utf-8",
    )
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 722, "height": 102})
    page.goto((ui / "hud.html").as_uri())
    page.wait_for_timeout(250)
    page.evaluate("() => { document.querySelector('#card').classList.add('no-orb'); health('Занят: songs_list — 50 с'); }")
    page.wait_for_timeout(80)
    result = page.evaluate("""() => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect();
      const notice = rect('#health'), topbar = rect('#assistant-topbar'), bar = rect('#bar');
      return {noticeTop: notice.top, noticeBottom: notice.bottom, topbarBottom: topbar.bottom, barTop: bar.top};
    }""")
    assert result["noticeTop"] >= result["topbarBottom"], result
    assert result["noticeBottom"] <= result["barTop"], result
    browser.close()
print("OK: health notice does not overlap the HUD top bar or first toolbar row")
