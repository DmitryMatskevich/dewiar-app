"""HUD draft copy/clear, clipboard fallbacks and right-aligned responsive actions."""
import os
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui = Path(folder) / "ui"
    shutil.copytree(ROOT / "ui", ui)
    (ui / "assets/dewiar.js").write_text('''
      window.sent=[];window.calls=[];window.copied=[];
      window.api=()=>({});window.core={connected:true,_on:{},
        on(k,f){(this._on[k]??=[]).push(f);return this},
        async call(cmd,args){calls.push([cmd,args]);return {}},
        async sendLater(cmd,args){sent.push([cmd,args]);return {}}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
        async writeText(text){copied.push(text)}}});
    ''')
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page(viewport={"width": 640, "height": 850})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto((ui / "hud.html").as_uri())
    page.wait_for_function("typeof writeClipboardText==='function'")
    page.evaluate("openAsk();applyLook({skin:'neusky'});showCap('','')")
    page.evaluate("""() => {
      const llm = {auto_select:true,active:'backup',profiles:{
        primary:{model:'first-model',priority:1},
        backup:{model:'backup-model',priority:9}
      }};
      core.call = async (cmd, args) => {
        if (cmd === 'settings_save') {
          Object.assign(llm, args.patch.llm);
          return {llm};
        }
        if (cmd === 'models_routing_status') {
          return {automatic:llm.auto_select,next:'primary',selected:{model:'previous-answer'}};
        }
        return {};
      };
      setModelSettings(llm);
      return refreshModelHint();
    }""")
    model = page.locator("#ask-model")
    assert model.inner_text() == "first-model"
    model.click()
    page.locator("#model-menu").get_by_role("button", name="backup-model").click()
    page.wait_for_function("document.querySelector('#ask-model.manual svg') !== null")
    assert model.inner_text() == "backup-model"
    model.click()
    page.locator("#model-menu").get_by_role("button", name="Автовыбор по приоритету").click()
    page.wait_for_function("document.querySelector('#ask-model').textContent === 'first-model'")
    assert not model.locator("svg").count()
    model.click()
    page.keyboard.press("Escape")
    assert page.locator("#model-menu").is_hidden()
    assert model.get_attribute("aria-expanded") == "false"
    field, clear, copy = [page.locator(s) for s in ["#askin", "#a-clear", "#a-copy"]]
    assert clear.is_disabled() and copy.is_disabled()
    text = "  Привет 😀\nВторая строка\n  "
    field.fill(text)
    field.evaluate("e=>e.setSelectionRange(2,8)")
    copy.click()
    page.wait_for_function("document.querySelector('#a-copy').classList.contains('copied')")
    assert page.evaluate("copied") == [text]
    assert field.input_value() == text
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [2,8]
    assert page.evaluate("sent.length") == 0
    assert "Весь текст" in page.locator("#ask-feedback").inner_text()
    # Native WebViews may reject Clipboard API. The fallback copies everything
    # and restores focus/selection instead of leaving a hidden textarea selected.
    page.evaluate("""() => {
      navigator.clipboard.writeText=async()=>{throw Error('denied')};
      window.fallback=[];document.execCommand=cmd=>{
        fallback.push([cmd,document.activeElement.value]);return true};
    }""")
    field.focus()
    field.evaluate("e=>e.setSelectionRange(4,9,'backward')")
    page.evaluate("document.querySelector('#a-copy').click()")
    page.wait_for_function("fallback.length===1")
    assert page.evaluate("fallback[0]") == ["copy", text]
    assert page.evaluate("document.activeElement.id") == "askin"
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd,e.selectionDirection]") == [4,9,"backward"]
    assert field.input_value() == text
    # Failed fallback must be reported rather than marked as copied.
    page.evaluate("window.copyError='';window.showErr=e=>copyError=e.message;document.execCommand=()=>false;document.querySelector('#a-copy').click()")
    page.wait_for_function("!!copyError")
    assert "Не удалось скопировать" in page.evaluate("copyError")
    assert not copy.evaluate("e=>e.classList.contains('copied')")
    assert field.input_value() == text
    # Clearing affects only the draft. Dictation and attached files survive.
    page.evaluate("atts=[{kind:'image',url:'data:image/png;base64,',name:'fixture.png'}];fire('dictation',{state:'recording'})")
    clear.click()
    assert field.input_value() == ""
    assert clear.is_disabled() and copy.is_disabled()
    assert page.evaluate("document.activeElement.id") == "askin"
    assert page.evaluate("atts.length") == 1
    assert page.evaluate("dictating")
    assert page.evaluate("sent.length") == 0
    page.evaluate("fire('dictation',{state:'partial',text:'Новая фраза'})")
    assert field.input_value() == "Новая фраза"
    assert not copy.is_disabled() and not clear.is_disabled()
    # Whitespace is still text and is copied without trimming.
    field.fill(" \n ")
    assert not copy.is_disabled()
    page.evaluate("navigator.clipboard.writeText=async text=>copied.push(text)")
    copy.click()
    page.wait_for_function("copied.at(-1)===' \\n '")

    page.evaluate("document.querySelector('#ask-model').textContent='provider/a-very-long-model-name';showCap('','')")
    for skin in ["neusky", "neudark"]:
        page.evaluate("skin=>applyLook({skin})", skin)
        for width in [340, 420, 640, 900]:
            page.set_viewport_size({"width": width, "height": 850})
            for recording in [False, True]:
                page.evaluate("on=>dictationUi(on)", recording)
                for state in ["listening", "thinking"]:
                    page.evaluate("state=>setRunControls(state)", state)
                    page.wait_for_timeout(30)
                    layout = page.evaluate("""() => {
                      const box=document.querySelector('#askbox').getBoundingClientRect();
                      const ids=['a-att','a-shot','ask-model','b-voice-inline','a-mic','a-mic-pick','a-clear','a-copy',document.querySelector('#a-send').getBoundingClientRect().width?'a-send':'a-stop'];
                      return {box:{left:box.left,right:box.right},buttons:ids.map(id=>{
                        const r=document.getElementById(id).getBoundingClientRect();
                        return {id,left:r.left,right:r.right,top:r.top,bottom:r.bottom}}),
                        overflow:document.querySelector('.askrow').scrollWidth>document.querySelector('.askrow').clientWidth+1};
                    }""")
                    buttons = layout["buttons"]
                    assert 0 <= layout["box"]["right"] - buttons[-1]["right"] <= 9, (width, layout)
                    assert not layout["overflow"], (width, layout)
                    for i, button in enumerate(buttons):
                        assert layout["box"]["left"] <= button["left"] < button["right"] <= layout["box"]["right"], (width, layout)
                        if i:
                            assert buttons[i-1]["right"] <= button["left"] + 1, (width, layout)
                            assert abs((buttons[i-1]["top"]+buttons[i-1]["bottom"])-(button["top"]+button["bottom"])) < 1, (width, layout)
    page.set_viewport_size({"width": 640, "height": 850})
    page.evaluate("applyLook({skin:'neusky'});dictationUi(false);setRunControls('idle');clearTimeout(draftCopiedTimer);document.querySelector('#a-copy').classList.remove('copied')")
    field.fill("Привет. Очень важно.")
    shot = ROOT / "work/hud-draft-actions.png"
    page.wait_for_timeout(350)  # Finish the existing skin transition before visual QA.
    page.screenshot(path=str(shot))
    assert not errors, errors
    browser.close()
print("ALL OK: full draft copy/fallback/error, clear without attachment loss, ongoing dictation and right-edge actions")
