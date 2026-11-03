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
        page.evaluate("""() => {STATE.on=true;STATE.meeting={id:'first',record:true,dual:true,started:Date.now()/1000,title:'Тест записи'};
          STATE.recording={...STATE.recording,enabled:true,levels:{me:{peak:8000},them:{peak:12000}},underruns:2};
          core._on.prompter.forEach(f=>f({kind:'state',state:STATE}));}""")
        assert page.locator("#recording-levels").is_visible()
        assert page.locator("#track-me").evaluate("e=>e.value") == 8000
        assert page.locator("#track-them").evaluate("e=>e.value") == 12000
        assert "2" in page.locator("#recording-diagnostics").inner_text()
        page.screenshot(path=str(Path(tempfile.gettempdir()) / "dewiar-meeting-tracks.png"))
        page.evaluate("STATE.on=false;STATE.can_start=true;STATE.summary_busy=true;STATE.processing=true;STATE.recording.export_busy=true;core._on.prompter.forEach(f=>f({kind:'state',state:STATE}))")
        assert page.locator("#again").is_enabled(), "New meeting must not wait for video/summary"
        assert page.locator("#done").is_visible(), "Processing result must remain dismissible"
        assert "создаётся в фоне" in page.locator("#meeting-video-result").inner_text()
        page.evaluate("STATE.recording.export_busy=false;STATE.recording.video_file='Встречи/Видео/test.mp4';core._on.prompter.forEach(f=>f({kind:'state',state:STATE}))")
        button = page.locator("#create-meeting-video")
        assert button.inner_text() == "Открыть видео"
        button.click()
        assert page.evaluate("CALLS.some(x=>x[0]==='ws_open' && x[1].path==='Встречи/Видео/test.mp4')")
        assert page.locator("#sum .wait").is_visible(), "Ready video is usable before summary"
        assert not page.evaluate("CALLS.some(x=>x[0]==='prompter_video')")
        page.evaluate("core._on.prompter.forEach(f=>f({kind:'summary',summary:'**Аудиозапись:** [Аудио/test.wav](dwr-file:Аудио/test.wav)\\n\\n**Видео:** [Видео/test.mp4](dwr-file:Видео/test.mp4)',busy:false}))")
        media = page.locator("#sum .meeting-media-actions button")
        assert media.count() == 2
        media.nth(0).click()
        assert page.evaluate("CALLS.some(x=>x[0]==='ws_open' && x[1].path==='Встречи/Аудио/test.wav')")
        page.evaluate("STATE.recording.video_file='';STATE.recording.video_error='Ошибка кодирования';core._on.prompter.forEach(f=>f({kind:'state',state:STATE}))")
        assert button.inner_text() == "Повторить создание видео"
        button.click()
        assert button.is_disabled()
        assert page.evaluate("CALLS.some(x=>x[0]==='prompter_video')")
        page.evaluate("core._on.prompter.forEach(f=>f({kind:'video',busy:false,path:'/tmp/Видео встречи.mp4',file:'Встречи/Видео/test.mp4'}))")
        assert button.is_enabled()
        assert "Видео готово" in page.locator("#meeting-video-result").inner_text()
        page.evaluate("core._on.prompter.forEach(f=>f({kind:'video',busy:false,error:'Нет аудиозаписи'}))")
        assert "Нет аудиозаписи" in page.locator("#meeting-video-result").inner_text()
        page.locator("#again").click()
        page.evaluate("core._on.prompter.forEach(f=>f({kind:'summary',meeting_id:'first',summary:'Background result',busy:false}))")
        assert page.locator("#setup").is_visible(), "Background summary must not interrupt new meeting setup"
        page.evaluate("STATE.on=true;STATE.meeting={id:'second',title:'Second'};core._on.prompter.forEach(f=>f({kind:'state',state:STATE}));core._on.prompter.forEach(f=>f({kind:'summary',meeting_id:'first',summary:'Old result',busy:false}))")
        assert page.locator("#live").is_visible(), "Old results must not overwrite the active session"
        page.evaluate("STATE.on=false;STATE.summary_busy=false;STATE.recording={}; core._on.prompter.forEach(f=>f({kind:'state',state:STATE}));core._on.prompter.forEach(f=>f({kind:'summary',meeting_id:'second',summary:'Без кадров',busy:false}))")
        assert not page.locator("#create-meeting-video").is_visible(), "Кнопка не должна быть без сохранённых кадров"
        assert not errors, errors
        browser.close()
    print("OK: consent, parallel progress, ready video playback, retry, new-meeting isolation")
finally:
    shutil.rmtree(folder, ignore_errors=True)
