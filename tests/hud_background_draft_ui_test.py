"""Background dictation remains editable and named copy works in normal dictation."""
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui=Path(folder)/'ui'; shutil.copytree(ROOT/'ui', ui)
    (ui/'assets/dewiar.js').write_text('''
window.calls=[];window.openedAccess=0;window.api=()=>({open_accessibility_settings:async()=>{openedAccess++}});
window.core={connected:true,_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},
 async call(cmd,a){calls.push([cmd,a]);if(cmd==='background_dictation_edit')return {active:true,session:a.session,revision:a.revision+1,text:a.text};if(cmd==='dictation_copy_text')return window.pendingPaste||window.pasteResult||{copied:true,inserted:false};return {active:false}},
 async sendLater(){return {}}};window.fire=(t,d)=>(core._on[t]||[]).forEach(f=>f(d));
''')
    browser=pw.chromium.launch();page=browser.new_page(viewport={'width':640,'height':850})
    errors=[];page.on('pageerror', lambda error:errors.append(str(error)))
    page.goto((ui/'hud.html').as_uri());page.wait_for_function("typeof renderBackgroundDictation==='function'")
    page.evaluate("openAsk();document.getElementById('askin').value='Существующий текст';fire('background_dictation',{active:true,state:'listening',session:4,revision:1,text:'Голосовой черновик'})")
    field=page.locator('#background-draft')
    assert field.is_visible() and field.input_value()=='Голосовой черновик'
    assert page.locator('#askin').input_value()=='Существующий текст'
    field.fill('Исправленный текст')
    page.wait_for_function("calls.some(x=>x[0]==='background_dictation_edit'&&x[1].text==='Исправленный текст')")
    page.evaluate("fire('background_dictation',{active:true,state:'copied',session:4,revision:2,text:'Исправленный текст'})")
    assert 'скопирован' in page.locator('#background-dictation-status').inner_text()
    assert field.input_value()=='Исправленный текст'
    page.evaluate("fire('background_dictation',{active:false,state:'stopped'});fire('dictation',{state:'recording',session:5});fire('dictation',{state:'partial',session:5,text:'диктовка'});fire('dictation',{state:'copy_requested',session:5})")
    page.wait_for_function("calls.some(x=>x[0]==='dictation_copy_text')")
    assert page.evaluate("calls.filter(x=>x[0]==='dictation_copy_text').at(-1)[1].text").endswith('диктовка')
    assert page.evaluate('dictating')
    assert page.locator('#background-draft').is_hidden()
    page.evaluate("fire('dictation',{state:'done',session:5,submit:false});fire('draft_command',{action:'copy'})")
    page.wait_for_function("calls.some(x=>x[0]==='dictation_copy_text'&&x[1].copy_only===true)")
    assert page.evaluate("calls.filter(x=>x[0]==='dictation_copy_text').at(-1)[1].text").endswith('диктовка')
    assert not page.evaluate('dictating')
    page.locator('#askin').fill('Первый лишний последний')
    page.locator('#askin').evaluate("e=>e.setSelectionRange(7,13)")
    page.evaluate("fire('draft_command',{action:'clear'})")
    assert page.locator('#askin').input_value() == 'Первый  последний'
    page.locator('#askin').evaluate("e=>e.setSelectionRange(0,0)")
    page.evaluate("fire('draft_command',{action:'clear'})")
    assert page.locator('#askin').input_value() == ''
    page.evaluate("fire('background_dictation',{active:true,state:'listening',session:6,revision:1,text:'До лишнее после'})")
    field.evaluate("e=>e.setSelectionRange(3,9)")
    page.evaluate("fire('background_dictation',{active:true,state:'clear_requested',session:6,revision:1,text:'До лишнее после'})")
    assert field.input_value() == 'До  после'
    page.wait_for_function("calls.some(x=>x[0]==='background_dictation_edit'&&x[1].text==='До  после')")
    page.evaluate("fire('background_dictation',{active:false,state:'stopped',session:7,revision:3,text:'Сохранённый текст'})")
    assert field.is_visible() and field.input_value() == 'Сохранённый текст'
    page.evaluate("fire('draft_command',{action:'copy'})")
    page.wait_for_function("calls.filter(x=>x[0]==='dictation_copy_text').at(-1)[1].text==='Сохранённый текст'")
    # The reason for a failed paste and the receiving application are shown, not a generic phrase.
    page.evaluate("fire('background_dictation',{active:true,state:'copied',session:8,revision:1,text:'Т',reason:'Нет разрешения «Универсальный доступ»'})")
    status=page.locator('#background-dictation-status').inner_text()
    assert 'не вставлен' in status and 'Универсальный доступ' in status, status
    page.evaluate("fire('background_dictation',{active:true,state:'inserted',session:8,revision:2,text:'',app:'Заметки'})")
    assert 'вставки отправлена в «Заметки»' in page.locator('#background-dictation-status').inner_text()
    page.evaluate("fire('background_dictation',{active:false,state:'stopped',session:9,revision:3,text:'Готовый текст'});pasteResult={copied:true,inserted:true,app:'Telegram'};fire('draft_command',{action:'insert'})")
    page.wait_for_function("document.getElementById('ask-feedback').textContent.includes('«Telegram»')")
    assert field.is_hidden()
    page.locator('#askin').fill('Текст для вставки')
    page.evaluate("pasteResult={copied:true,inserted:false,reason:'Нет фокуса'};fire('draft_command',{action:'insert'})")
    page.wait_for_function("document.getElementById('ask-feedback').textContent.includes('Нет фокуса')")
    assert page.locator('#askin').input_value() == 'Текст для вставки'
    page.evaluate("pasteResult={copied:true,inserted:false};fire('draft_command',{action:'copy'})")
    page.wait_for_function("document.getElementById('ask-feedback').textContent.includes('Текст скопирован')")
    assert page.locator('#askin').input_value() == 'Текст для вставки'
    page.evaluate("pendingPaste=new Promise(resolve=>window.finishPaste=resolve);fire('draft_command',{action:'insert'})")
    page.locator('#askin').fill('Текст для вставки Новая фраза')
    page.evaluate("finishPaste({copied:true,inserted:true,app:'Telegram'});pendingPaste=null")
    page.wait_for_function("document.getElementById('askin').value==='Новая фраза'")
    page.evaluate("pendingPaste=new Promise(resolve=>window.finishPaste=resolve);fire('draft_command',{action:'insert'})")
    page.locator('#askin').fill('Исправленный текст')
    page.evaluate("finishPaste({copied:true,inserted:true,app:'Telegram'});pendingPaste=null")
    page.wait_for_timeout(100)
    assert page.locator('#askin').input_value() == 'Исправленный текст'
    page.evaluate("fire('dictation',{state:'recording',session:10});pasteResult={copied:true,inserted:true,app:'Telegram'};fire('dictation',{state:'copy_requested',action:'insert',session:10})")
    page.wait_for_function("document.getElementById('askin').value==='' ")
    assert page.evaluate('dictating')
    page.evaluate("fire('voice_command',{action:'command',status:'received',command:'открой таблицу'})")
    notice=page.locator('#voice-command-feedback')
    assert notice.is_visible() and 'открой таблицу' in notice.inner_text() and 'Выполняю' in notice.inner_text()
    page.evaluate("fire('voice_command',{action:'stop',status:'done'})")
    assert 'Голосовой ввод остановлен' in notice.inner_text()
    page.locator('#askin').fill('Не терять')
    page.evaluate("pasteResult={copied:true,inserted:false,reason:'Нет разрешения «Универсальный доступ»',reason_code:'accessibility'};fire('draft_command',{action:'insert'})")
    page.wait_for_function("document.getElementById('voice-command-message').textContent.includes('Нет разрешения')")
    assert 'Команда «вставить» получена' in notice.inner_text()
    assert page.locator('#askin').input_value() == 'Не терять'
    assert page.locator('#voice-command-access').is_visible()
    page.locator('#voice-command-access').click()
    assert page.evaluate('openedAccess') == 1
    page.evaluate("fire('voice_command',{action:'insert',status:'error',message:'Нет фокуса'})")
    assert page.locator('#voice-command-access').is_hidden()
    page.evaluate("fire('voice_command',{action:'copy',status:'done'})")
    assert 'Текст скопирован' in notice.inner_text()
    page.locator('#voice-command-dismiss').click()
    assert notice.is_hidden()
    assert not errors,errors
    browser.close()
print('OK: editable background draft, copy feedback and voice copy in normal dictation')
