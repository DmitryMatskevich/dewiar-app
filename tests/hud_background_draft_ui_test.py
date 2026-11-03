"""Background dictation remains editable and named copy works in normal dictation."""
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui=Path(folder)/'ui'; shutil.copytree(ROOT/'ui', ui)
    (ui/'assets/dewiar.js').write_text('''
window.calls=[];window.api=()=>({});
window.core={connected:true,_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},
 async call(cmd,a){calls.push([cmd,a]);if(cmd==='background_dictation_edit')return {active:true,session:a.session,revision:a.revision+1,text:a.text};if(cmd==='dictation_copy_text')return {copied:true,inserted:false};return {active:false}},
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
    assert not errors,errors
    browser.close()
print('OK: editable background draft, copy feedback and voice copy in normal dictation')
