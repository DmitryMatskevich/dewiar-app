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
        calls.push([cmd,args]);
        if (cmd === 'dictation_finish' && window.delayStop) return new Promise(resolve=>window.resolveStop=resolve);
        if (cmd === 'settings_save') {
          Object.assign(llm, args.patch.llm);
          return {llm};
        }
        if (cmd === 'models_routing_status') {
          return {automatic:llm.auto_select,next:'primary',order:['primary','backup'],selected:{model:'previous-answer'}};
        }
        return {};
      };
      setModelSettings(llm);
      return refreshModelHint();
    }""")
    model = page.locator("#ask-model")
    assert model.inner_text() == "first-model"
    model.click()
    auto = page.locator("#model-menu").get_by_role("button", name="Автовыбор по приоритету")
    assert auto.get_attribute("title").splitlines() == ["1. first-model (приоритет 1)", "2. backup-model (приоритет 9)"]
    assert page.locator('[data-model-id="primary"]').get_attribute("aria-pressed") == "true"
    page.locator("#model-menu").get_by_role("button", name="backup-model").click()
    page.wait_for_function("document.querySelector('#ask-model').textContent === 'backup-model'")
    assert "выбрана модель «backup-model»" in page.locator("#cap").inner_text()
    assert model.inner_text() == "backup-model"
    assert not model.locator("svg").count()
    model.click()
    assert page.locator('[data-model-id="backup"]').get_attribute("aria-pressed") == "true"
    page.locator("#model-menu").get_by_role("button", name="Автовыбор по приоритету").click()
    page.wait_for_function("document.querySelector('#ask-model').textContent === 'first-model'")
    assert not model.locator("svg").count()
    page.evaluate("document.querySelector('#ask').style.display='none';document.querySelector('#b-tasks').hidden=false")
    assert model.is_visible()
    model.click()
    assert page.locator("#model-menu").is_visible()
    page.keyboard.press("Escape")
    page.evaluate("document.querySelector('#b-tasks').hidden=true;openAsk()")
    model.click()
    page.keyboard.press("Escape")
    assert page.locator("#model-menu").is_hidden()
    assert model.get_attribute("aria-expanded") == "false"
    field, clear, copy = [page.locator(s) for s in ["#askin", "#a-clear", "#a-copy"]]
    assert clear.is_hidden() and copy.is_hidden()
    layout = page.locator('#a-layout')
    assert layout.is_disabled()
    field.fill('До Ghbdtn, vbh! Привет 😀 42 После')
    field.evaluate("e=>{e.focus();e.setSelectionRange(3,e.value.length-6);e.dispatchEvent(new Event('select'))}")
    assert layout.is_enabled()
    layout.click()
    assert field.input_value() == 'До Привет, мир! Ghbdtn 😀 42 После'
    assert field.evaluate("e=>e.value.slice(e.selectionStart,e.selectionEnd)") == 'Привет, мир! Ghbdtn 😀 42'
    layout.focus(); page.keyboard.press('Enter')
    assert field.input_value() == 'До Ghbdtn, vbh! Привет 😀 42 После'
    field.evaluate("e=>{e.setSelectionRange(0,0);e.dispatchEvent(new Event('select'))}")
    assert layout.is_disabled()
    field.fill('')
    text = "  Привет 😀\nВторая строка\n  "
    field.fill(text)
    field.evaluate("e=>e.setSelectionRange(2,8)")
    copy.click()
    page.wait_for_function("document.querySelector('#a-copy').classList.contains('copied')")
    assert page.evaluate("copied") == ["Привет"]
    assert field.input_value() == text
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [2,8]
    assert page.evaluate("sent.length") == 0
    assert "Выделенный текст" in page.locator("#ask-feedback").inner_text()
    field.evaluate("e=>e.setSelectionRange(0,0)")
    copy.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("copied.length===2")
    assert page.evaluate("copied[1]") == text
    assert "Весь текст" in page.locator("#ask-feedback").inner_text()
    # Native WebViews may reject Clipboard API. The fallback copies the selected text
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
    assert page.evaluate("fallback[0]") == ["copy", text[4:9]]
    assert page.evaluate("document.activeElement.id") == "askin"
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd,e.selectionDirection]") == [4,9,"backward"]
    assert field.input_value() == text
    # Failed fallback must be reported rather than marked as copied.
    page.evaluate("window.copyError='';window.showErr=e=>copyError=e.message;document.execCommand=()=>false;document.querySelector('#a-copy').click()")
    page.wait_for_function("!!copyError")
    assert "Не удалось скопировать" in page.evaluate("copyError")
    assert not copy.evaluate("e=>e.classList.contains('copied')")
    assert field.input_value() == text
    # Erase only the selected UTF-16 range, preserving whitespace and attachments.
    page.evaluate("atts=[{kind:'image',url:'data:image/png;base64,',name:'fixture.png'}]")
    field.fill("Начало 😀 конец\n ")
    field.evaluate("e=>e.setSelectionRange(7,10)")
    clear.click()
    assert field.input_value() == "Начало конец\n "
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [7,7]
    assert clear.is_visible() and copy.is_visible()
    # A collapsed selection erases the whole draft; keyboard activation works too.
    clear.focus()
    page.keyboard.press("Enter")
    assert field.input_value() == ""
    assert clear.is_hidden() and copy.is_hidden()
    assert page.evaluate("atts.length") == 1
    # Stop keeps queued results, even before the stop command returns.
    page.evaluate("window.delayStop=true;fire('dictation',{state:'recording',session:1})")
    mic = page.locator("#a-mic")
    assert mic.is_enabled() and copy.is_hidden()
    assert "Остановить голосовой ввод" in mic.get_attribute("aria-label")
    mic.click()
    page.wait_for_function("typeof resolveStop==='function'")
    assert mic.is_disabled()
    page.evaluate("fire('dictation',{state:'partial',session:1,text:'Последняя фраза'});fire('dictation',{state:'done',session:1,submit:false})")
    assert field.input_value() == "Последняя фраза" and page.evaluate("sent.length") == 0
    page.evaluate("resolveStop({ok:true});window.delayStop=false")
    page.wait_for_function("!dictating")
    field.fill("")
    assert clear.is_hidden() and copy.is_hidden()
    assert page.evaluate("calls.at(-1)[0]") == "dictation_finish"
    # Stop during final recognition preserves existing text, its selection and files.
    field.fill(text)
    field.evaluate("e=>e.setSelectionRange(4,9)")
    page.evaluate("fire('dictation',{state:'recording',session:3});fire('dictation',{state:'finishing',session:3})")
    mic.click()
    page.evaluate("fire('dictation',{state:'done',session:3,submit:false})")
    page.wait_for_function("!dictating")
    assert field.input_value() == text
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [4,9]
    assert page.evaluate("document.activeElement.id") == "askin"
    assert page.evaluate("atts.length") == 1
    assert page.evaluate("sent.length") == 0
    # An old session cannot affect a new recording, including a late submit event.
    page.evaluate("fire('dictation',{state:'recording',session:5});fire('dictation',{state:'partial',session:3,text:'Старая фраза'});fire('dictation',{state:'done',session:3,submit:true})")
    assert field.input_value() == text and page.evaluate("dictating")
    assert page.evaluate("sent.length") == 0
    field.evaluate("e=>e.setSelectionRange(e.value.length,e.value.length)")
    page.evaluate("fire('dictation',{state:'partial',session:5,text:'Новая фраза'});fire('dictation',{state:'done',session:5,submit:false})")
    assert field.input_value() == text + "Новая фраза"
    assert clear.is_visible() and copy.is_visible()
    # Whitespace is still text and is copied without trimming.
    field.fill(" \n ")
    assert not copy.is_disabled()
    page.evaluate("navigator.clipboard.writeText=async text=>copied.push(text)")
    copy.click()
    page.wait_for_function("copied.at(-1)===' \\n '")

    page.evaluate("document.querySelector('#ask-model').textContent='provider/a-very-long-model-name';showCap('','')")
    for skin in ["neusky", "neudark"]:
        page.evaluate("skin=>applyLook({skin})", skin)
        for width in [300, 340, 375, 420, 640, 900]:
            page.set_viewport_size({"width": width, "height": 850})
            for recording in [False, True]:
                page.evaluate("on=>dictationUi(on)", recording)
                for state in ["listening", "thinking"]:
                    page.evaluate("state=>setRunControls(state)", state)
                    page.wait_for_timeout(30)
                    layout = page.evaluate("""() => {
                      const box=document.querySelector('#askbox').getBoundingClientRect();
                      const ids=['a-att','b-voice-inline','a-mic','a-mic-pick','a-layout','a-clear','a-copy','a-send'].filter(id=>document.getElementById(id).getBoundingClientRect().width);
                      return {box:{left:box.left,right:box.right},buttons:ids.map(id=>{
                        const r=document.getElementById(id).getBoundingClientRect();
                        return {id,left:r.left,right:r.right,top:r.top,bottom:r.bottom}}),
                        overflow:document.querySelector('.askrow').scrollWidth>document.querySelector('.askrow').clientWidth+1};
                    }""")
                    buttons = layout["buttons"]
                    assert 0 <= layout["box"]["right"] - buttons[-1]["right"] <= 9, (width, layout)
                    assert not layout["overflow"], (width, layout)
                    top = page.evaluate("""() => {
                      const model=document.querySelector('#ask-model').getBoundingClientRect();
                      const left=document.querySelector('#corner-l').getBoundingClientRect();
                      const right=document.querySelector('#corner-r').getBoundingClientRect();
                      return {center:(model.left+model.right)/2, left:model.left, right:model.right, leftEnd:left.right, rightStart:right.left, top:model.top};
                    }""")
                    assert abs(top['center'] - width / 2) < 1 and top['top'] == 10, (width, top)
                    assert top['left'] >= top['leftEnd'] and top['right'] <= top['rightStart'], (width, top)
                    for i, button in enumerate(buttons):
                        assert layout["box"]["left"] <= button["left"] < button["right"] <= layout["box"]["right"], (width, layout)
                        if i:
                            previous = buttons[i-1]
                            assert previous["right"] <= button["left"] + 1, (width, layout)
                            assert abs((previous["top"]+previous["bottom"])-(button["top"]+button["bottom"])) < 1, (width, layout)
    page.set_viewport_size({"width": 640, "height": 850})
    page.evaluate("applyLook({skin:'neusky'});dictationUi(false);setRunControls('idle');clearTimeout(draftCopiedTimer);document.querySelector('#a-copy').classList.remove('copied')")
    field.fill("Привет. Очень важно.")
    shot = ROOT / "work/hud-draft-actions.png"
    page.wait_for_timeout(350)  # Finish the existing skin transition before visual QA.
    page.screenshot(path=str(shot))
    assert not errors, errors
    browser.close()
print("ALL OK: hidden empty actions, selected/full copy and fallback, selected/full erase, cancelled dictation, stale results and responsive actions")
