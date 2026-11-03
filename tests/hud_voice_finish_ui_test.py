"""Real HUD: multiline input, voice completion, draft retention and contextual help."""
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
      window.sent=[];window.calls=[];
      window.api=()=>({});
      window.core={connected:true,_on:{},
        on(k,f){(this._on[k]??=[]).push(f);return this},
        async call(cmd,args){calls.push([cmd,args]);return {}},
        async sendLater(cmd,args){sent.push([cmd,args]);return {}}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));
    ''')
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page(viewport={"width": 640, "height": 850})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto((ui / "hud.html").as_uri())
    page.wait_for_function("typeof sendAsk === 'function'")
    page.evaluate("""() => {
      personas=[{id:'lira',name:'Лира',color:'#5588ff',style:'aura'},
                {id:'patrik',name:'Патрик',color:'#5588ff',style:'aura'}];
      applyPersona('lira');openAsk();
    }""")
    field = page.locator("#askin")
    hint = field.get_attribute("title")
    assert "Ctrl+Enter" not in hint
    for word in ["Enter — новая строка", "стрелкой вверх", "«Лира»", "отдельно"]:
        assert word in hint, (word, hint)
    field.fill("Первая строка")
    field.press("End")
    field.press("Enter")
    field.type("Вторая строка")
    assert field.input_value() == "Первая строка\nВторая строка"
    assert page.evaluate("sent.length") == 0
    page.locator("#a-send").click()
    assert page.evaluate("sent.at(-1)[1].text") == "Первая строка\nВторая строка"

    # Keep old shortcuts compatible, but don't advertise them as the only way to send.
    for shortcut in ["Control+Enter", "Meta+Enter"]:
        field.fill(shortcut)
        field.press(shortcut)
        assert page.evaluate("sent.at(-1)[1].text") == shortcut

    page.evaluate("applyPersona('patrik')")
    assert "«Патрик»" in field.get_attribute("title")
    page.evaluate("fire('dictation',{state:'recording'})")
    assert "имя текущего помощника" in page.locator("#a-mic").get_attribute("title")
    assert "«ввод»" not in hint and "«вот»" not in hint
    field.fill("Мой черновик.")
    count = page.evaluate("sent.length")
    page.evaluate("""() => {
      fire('dictation',{state:'partial',text:'Напиши письмо'});
      fire('dictation',{state:'finishing'});
    }""")
    assert page.locator("#a-mic").is_enabled()
    assert page.evaluate("sent.length") == count
    page.evaluate("fire('dictation',{state:'done',submit:true})")
    assert page.evaluate("sent.length") == count + 1
    assert page.evaluate("sent.at(-1)[1].text") == "Мой черновик. Напиши письмо"
    assert field.input_value() == ""
    # A one-word voice command without draft/attachments does not send an empty request.
    page.evaluate("fire('dictation',{state:'recording'});fire('dictation',{state:'done',submit:true})")
    assert page.evaluate("sent.length") == count + 1
    # Manual microphone stop retains the recognised draft, without sending it.
    page.evaluate("fire('dictation',{state:'recording'});fire('dictation',{state:'partial',text:'Оставить в поле'})")
    page.locator("#a-mic").click()
    assert page.evaluate("calls.at(-1)[0]") == "dictation_cancel"
    page.wait_for_function("!dictating")
    assert field.input_value() == "Оставить в поле"
    assert page.evaluate("sent.length") == count + 1
    # Each asynchronous result uses the live caret, including after a manual move.
    field.fill("Начало конец.")
    field.evaluate("e=>e.setSelectionRange(7,7)")
    page.locator("#a-mic").click()
    page.evaluate("fire('dictation',{state:'partial',text:'середина'})")
    assert field.input_value() == "Начало середина конец."
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [7,15]
    page.evaluate("fire('dictation',{state:'partial',text:'ещё'})")
    assert field.input_value() == "Начало середина ещё конец."
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [16,19]
    field.evaluate("e=>e.setSelectionRange(0,0)")
    page.evaluate("fire('dictation',{state:'partial',text:'Вступление.'})")
    assert field.input_value() == "Вступление. Начало середина ещё конец."
    assert field.evaluate("e=>e.selectionStart") == 0
    # Selecting a word replaces only that word; punctuation and suffix survive.
    field.fill("Привет, старый мир!")
    field.evaluate("e=>e.setSelectionRange(8,14)")
    page.evaluate("fire('dictation',{state:'partial',text:'новый'})")
    assert field.input_value() == "Привет, новый мир!"
    assert field.evaluate("e=>[e.selectionStart,e.selectionEnd]") == [8,13]
    for initial, pos, phrase, expected in [
        ("А\nБ", 2, "новая строка", "А\nновая строка Б"),
        ("Список:   ", 10, "пункт", "Список:   пункт"),
        ("Текст!", 5, "вставка", "Текст вставка!"),
        ("(конец)", 1, "начало", "(начало конец)"),
        ("😀 конец", 3, "середина", "😀 середина конец"),
    ]:
        field.fill(initial)
        field.evaluate("(e,p)=>e.setSelectionRange(p,p)",pos)
        page.evaluate("text=>fire('dictation',{state:'partial',text})",phrase)
        assert field.input_value() == expected, (initial,field.input_value())
        assert field.evaluate("e=>e.value.slice(e.selectionStart,e.selectionEnd)") == phrase, expected
    page.evaluate("fire('dictation',{state:'finishing'});fire('dictation',{state:'done',submit:true})")
    assert page.evaluate("sent.at(-1)[1].text") == "😀 середина конец"
    field.hover()
    page.wait_for_timeout(400)
    assert "«Патрик»" in field.get_attribute("data-tip")
    assert "Ctrl+Enter" not in field.get_attribute("data-tip")
    # Keep a real pointer press on Stop while a recognition result moves the toolbar.
    field.fill("Короткий черновик.")
    field.evaluate("e=>e.setSelectionRange(e.value.length,e.value.length)")
    page.evaluate("fire('dictation',{state:'recording',session:12})")
    mic = page.locator("#a-mic")
    box = mic.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    phrase = "Последняя фраза, которую нужно прокрутить. " * 120 + "Конец."
    page.evaluate("text=>{setDictationLevel(.4);fire('dictation',{state:'partial',text,session:12})}", phrase)
    assert field.evaluate("e=>e.scrollTop + e.clientHeight >= e.scrollHeight - 2")
    page.mouse.up()
    page.wait_for_function("!dictating")
    assert page.evaluate("calls.at(-1)[0]") == "dictation_cancel"
    assert field.input_value().endswith("Конец.")
    assert field.evaluate("e=>e.scrollTop + e.clientHeight >= e.scrollHeight - 2")

    field.fill("")
    # Stop sits beside the assistant's answer and calls the existing stop command.
    page.evaluate("showCap('Вопрос','Ответ');setState('speaking')")
    stop = page.locator("#answer-row #a-stop")
    assert stop.is_visible() and page.locator("#ask #a-stop").count() == 0
    page.set_viewport_size({"width": 340, "height": 520})
    answer_box = page.locator("#bot").bounding_box()
    stop_box = stop.bounding_box()
    assert answer_box["x"] + answer_box["width"] <= stop_box["x"]
    assert stop_box["x"] + stop_box["width"] <= 340
    stop.click()
    assert page.evaluate("calls.at(-1)[0]") == "stop"
    assert stop.is_hidden()
    assert not errors, errors
    browser.close()
print("ALL OK: real HUD keyboard/voice send, insertion at live caret, selection replacement, cursor advance and help")
