"""UI defaults, explicit consent opt-out and asynchronous meeting-video action."""
import json
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
folder = Path(tempfile.mkdtemp(prefix="meeting-video-ui-"))
try:
    shutil.copytree(root / "ui", folder / "ui")
    (folder / "ui/assets/dewiar.js").write_text("""
window.CALLS = [];
window.api = () => ({});
window.STATE = {on:false, brain:true, meeting:{}, files:[], lines:[], hints:[], voice:{},
 recording:{can_export:true, export_busy:false, screenshots:1, video_frames:1}};
window.core={_on:{},on(k,f){(this._on[k] ||= []).push(f);},async call(cmd,a){
 CALLS.push([cmd,a]); if(cmd==='prompter_state') return STATE; return {busy:true};
}};
setTimeout(()=>core._on.hello?.forEach(f=>f({})),30);
""", encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 440, "height": 850})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto((folder / "ui/prompter.html").as_uri())
        page.wait_for_function("typeof st !== 'undefined' && st !== null")
        assert page.locator("#m-consent").is_checked()
        page.locator("#m-record").check()
        page.locator("#m-consent").uncheck()
        assert page.locator("#start").is_disabled()
        page.locator("#m-record").uncheck()
        page.locator("#m-record").check()
        assert not page.locator("#m-consent").is_checked(), "User's opt-out was overwritten"
        assert page.locator("#start").is_disabled()
        page.evaluate("core._on.prompter.forEach(f=>f({kind:'summary',summary:'**Аудиозапись:** [Аудио/test.wav](dwr-file:Аудио/test.wav)\\n\\n**Видео:** [Видео/test.mp4](dwr-file:Видео/test.mp4)',busy:false}))")
        media = page.locator("#sum .meeting-media-actions button")
        assert media.count() == 2
        media.nth(0).click()
        assert page.evaluate("CALLS.some(x=>x[0]==='ws_open' && x[1].path==='Встречи/Аудио/test.wav')")
        button = page.locator("#create-meeting-video")
        assert button.is_visible() and button.is_enabled()
        button.click()
        assert button.is_disabled()
        assert page.evaluate("CALLS.some(x=>x[0]==='prompter_video')")
        page.evaluate("core._on.prompter.forEach(f=>f({kind:'video',busy:false,path:'/tmp/Видео встречи.mp4'}))")
        assert button.is_enabled()
        assert "Видео встречи.mp4" in page.locator("#meeting-video-result").inner_text()
        page.evaluate("core._on.prompter.forEach(f=>f({kind:'video',busy:false,error:'Нет аудиозаписи'}))")
        assert "Нет аудиозаписи" in page.locator("#meeting-video-result").inner_text()
        page.evaluate("STATE.recording.screenshots=0; STATE.recording.video_frames=0; core._on.prompter.forEach(f=>f({kind:'summary',summary:'Без кадров',busy:false}))")
        assert not page.locator("#create-meeting-video").is_visible(), "Кнопка не должна быть без сохранённых кадров"
        assert not errors, errors
        browser.close()
    print("OK: default consent, preserved opt-out, export button and success/error UI")
finally:
    shutil.rmtree(folder, ignore_errors=True)
