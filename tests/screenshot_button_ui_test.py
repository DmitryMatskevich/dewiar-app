"""Real capture dispatch, assistant chat persistence and four card actions; no network."""
import base64
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from chatlog import ChatLog
from engine_commands import CommandsMixin
from workspace import Workspace
from settings import Settings

with tempfile.TemporaryDirectory() as folder,sync_playwright() as pw:
    root=Path(folder);settings=Settings(root/'settings.json')
    settings.update({'workspace':{'path':str(root/'workspace')}});ws=Workspace(settings)
    image=io.BytesIO();Image.new('RGB',(700,400),'white').save(image,'PNG');png=image.getvalue()
    text='Решение 42\nProject Alpha 123\n<script>bad()</script>\n' * 500
    events=[]; calls=[]
    engine=SimpleNamespace(workspace=ws,s=settings,chatlog=ChatLog(root/'chat.jsonl'),
        toolbox=SimpleNamespace(policy=Mock(return_value=(True,False))),note=Mock(),
        emit=lambda event_type,**data:events.append([event_type,data]))
    def call(cmd,args):
        calls.append((cmd,args))
        events.clear()
        with patch('eyes.capture_screen',return_value=png),patch('screenshot_sources.visible_windows',return_value=[{'window_id':42,'name':'Fixture','title':'Окно','id':'test','pid':1}]),patch('screenshot_sources.capture_window',return_value=png),patch('ocr.recognize_result',return_value={
                'status':'done','engine':'fixture','text':text,'lines':[]}):
            result=CommandsMixin.command(engine,cmd,args or {})
        return {'result':result,'events':list(events)}
    ui=root/'ui';shutil.copytree(ROOT/'ui',ui)
    (ui/'assets/dewiar.js').write_text('''
      window.opens=[];window.copiedImages=[];window.copiedTexts=[];window.screenGranted=false;window.screenSettings=0;
      window.api=()=>({async open_settings(...args){opens.push(args);return true;},
        async screen_capture_permission(){if(window.permissionError)throw Error('Unavailable');return {granted:screenGranted}},
        async open_screen_settings(){screenSettings++;return true},
        async copy_image_data(url){copiedImages.push(url);return true;}});
      window.core={snapshot:{},_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(cmd,args={}){
        if(!['screenshot_attach','screenshot_messages','screenshot_image','screenshot_text','screenshot_text_save','screenshot_to_note','screenshot_dismiss','screenshot_sources','screenshot_window_preview','screenshot_prepare','screenshot_draft','screenshot_draft_cancel'].includes(cmd))return {};
        if(cmd==='screenshot_text_save') {
          if(window.failSave)throw Error('Диск недоступен');
          if(window.deferSave){window.deferSave=false;await new Promise(resolve=>window.releaseSave=resolve);}
        }
        const r=await nativeCommand(cmd,args);for(const [kind,data] of r.events)fire(kind,data);return r.result;}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{async writeText(t){copiedTexts.push(t)}}});
    ''')
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':420,'height':850});page.expose_function('nativeCommand',call)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((ui/'hud.html').as_uri());page.evaluate("openAsk();applyLook({skin:'neusky'});showCap('','')")
    shot_button=page.locator('#a-shot')
    page.wait_for_function("document.querySelector('#a-shot').classList.contains('access-unavailable')")
    assert float(shot_button.locator('svg').evaluate("e=>getComputedStyle(e).opacity")) < .5
    assert 'Нет доступа' in shot_button.get_attribute('aria-label')
    shot_button.click()
    page.wait_for_function('screenSettings===1')
    assert not page.locator('.ss-source-menu').count()
    assert not any(cmd in ('screenshot_attach','screenshot_sources','screenshot_prepare') for cmd,_ in calls)
    page.evaluate("screenGranted=true;window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("!document.querySelector('#a-shot').classList.contains('access-unavailable')")
    # Permission can be revoked while HUD remains open; the click checks again.
    page.evaluate('screenGranted=false')
    shot_button.focus();page.keyboard.press('Enter')
    page.wait_for_function('screenSettings===2')
    assert not page.locator('.ss-source-menu').count()
    # An unavailable native check never presents the capture picker as permitted.
    page.evaluate("permissionError=true;window.dispatchEvent(new Event('focus'))")
    shot_button.click();page.wait_for_function('screenSettings===3')
    assert not page.locator('.ss-source-menu').count()
    page.evaluate("permissionError=false;screenGranted=true;window.dispatchEvent(new Event('pywebviewready'))")
    page.wait_for_function("!document.querySelector('#a-shot').classList.contains('access-unavailable')")
    page.locator('#a-shot').click()
    assert page.locator('.ss-source-menu').is_visible()
    page.wait_for_function("document.querySelector('.ss-window-preview')?.naturalWidth > 0")
    assert page.locator('.ss-window-select').get_attribute('aria-pressed')=='false'
    page.locator('.ss-window-preview-button').click()
    page.locator('.ss-large .ss-full-image').wait_for()
    assert page.locator('.ss-large .ss-full-image').evaluate('e=>e.naturalWidth')==700
    assert not engine.chatlog.page()['items'],'Preview must not attach a screenshot or run OCR'
    assert any(cmd=='screenshot_window_preview' and args.get('full_size') for cmd,args in calls)
    page.locator('.ss-large .ss-close').click()
    assert page.locator('.ss-source-menu').is_visible()
    assert not engine.chatlog.page()['items'],'Opening the menu must not capture'
    page.locator('[data-ss-source="screen"]').click()
    card=page.locator('#screenshot-messages .ss-card');card.wait_for()
    assert page.evaluate('atts.length')==0,'Assistant result must not be a user attachment'
    assert not list(ws.root.glob('Заметки/Снимки экрана/*.md'))
    assert page.locator('#b-notes-dot').is_hidden()
    item=engine.chatlog.page()['items'][0]
    assert item['role']=='assistant' and item['att'][0]['capture']['text']==text
    assert not any(kind=='note_saved' for kind,_ in events)
    assert not card.locator('details,summary,pre').count()
    assert card.locator('.ss-status').is_hidden()
    assert 'Текст распознан локально' not in card.inner_text()
    card.locator('[data-ss-open]').click()
    page.locator('.ss-full-image').wait_for()
    assert base64.b64decode(page.locator('.ss-full-image').get_attribute('src').split(',',1)[1])==png
    page.locator('.ss-dialog-bar button').click()
    card.locator('[data-ss-action="image"]').click()
    page.wait_for_function('copiedImages.length===1')
    assert base64.b64decode(page.evaluate('copiedImages[0]').split(',',1)[1])==png
    card.locator('[data-ss-action="text"]').click()
    editor=page.get_by_role('dialog',name='Редактирование текста снимка')
    field=editor.get_by_role('textbox',name='Текст снимка')
    assert field.input_value()==text
    assert page.evaluate('copiedTexts')==[], 'Opening the editor must not overwrite the clipboard'
    edited_text='Исправлено вручную\n<script>только текст</script>\n  '
    field.fill(edited_text)
    editor.get_by_role('button',name='Скопировать',exact=True).click()
    page.wait_for_function('copiedTexts.length===1')
    page.wait_for_function("document.querySelector('.ss-text-dialog [role=status]').textContent.includes('Сохранено')")
    assert page.evaluate('copiedTexts[0]')==edited_text
    assert engine.chatlog.screenshot(item['id'])['att'][0]['capture']['text']==edited_text
    # Native WebKit may deny Clipboard API; its fallback must stay inside the modal.
    page.evaluate("""window.clipboardWriter=navigator.clipboard.writeText;
      navigator.clipboard.writeText=async()=>{throw Error('denied')};
      document.execCommand=()=>{window.fallbackText=document.activeElement.value;
        return !!document.activeElement.closest('.ss-text-dialog')};""")
    field.evaluate('e=>e.setSelectionRange(0,5)')
    editor.get_by_role('button',name='Скопировать',exact=True).click()
    page.wait_for_function('typeof fallbackText===\'string\'')
    assert page.evaluate('fallbackText')==edited_text
    page.evaluate('navigator.clipboard.writeText=clipboardWriter')
    editor.get_by_role('button',name='Закрыть',exact=True).click();editor.wait_for(state='detached')
    engine.chatlog=ChatLog(root/'chat.jsonl')
    # A native bridge may receive hello before HUD subscribes; history must still load.
    page.reload();card.wait_for()
    page.evaluate("screenGranted=true;window.dispatchEvent(new Event('focus'))")
    card.locator('[data-ss-action="text"]').click()
    assert field.input_value()==edited_text, 'Edits must survive reloading the stored history'
    # A failed save leaves the editor/draft open; retry is explicit and recoverable.
    page.evaluate('failSave=true')
    field.fill('Несохранённый текст')
    editor.get_by_role('button',name='Закрыть',exact=True).click()
    page.wait_for_function("document.querySelector('.ss-text-dialog [role=status]').textContent.includes('Диск недоступен')")
    assert editor.is_visible() and field.input_value()=='Несохранённый текст'
    assert engine.chatlog.screenshot(item['id'])['att'][0]['capture']['text']==edited_text
    page.evaluate('failSave=false;deferSave=true')
    field.fill('Первая правка')
    editor.get_by_role('button',name='Сохранить',exact=True).click()
    page.wait_for_function("typeof releaseSave==='function'")
    # Type while saving and close: latest text must be serialized after the first write.
    edited_text='Последняя правка\n  Все пробелы сохранены.  '
    field.fill(edited_text)
    editor.get_by_role('button',name='Закрыть',exact=True).click()
    assert editor.is_visible()
    page.evaluate('releaseSave()');editor.wait_for(state='detached')
    assert engine.chatlog.screenshot(item['id'])['att'][0]['capture']['text']==edited_text
    # Debounced autosave works without a Save/Close click, including narrow windows.
    card.locator('[data-ss-action="text"]').click()
    edited_text += '\nАвтосохранение.'
    field.fill(edited_text)
    page.wait_for_function("document.querySelector('.ss-text-dialog [role=status]').textContent==='Сохранено'")
    assert engine.chatlog.screenshot(item['id'])['att'][0]['capture']['text']==edited_text
    page.set_viewport_size({'width':340,'height':600})
    assert editor.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
    assert field.bounding_box()['height']>=100
    page.screenshot(path=str(ROOT/'work/screenshot-text-editor.png'))
    page.keyboard.press('Escape');editor.wait_for(state='detached')
    card.locator('[data-ss-action="note"]').click()
    page.wait_for_function('unreadNotes.size===1')
    notes=list(ws.root.glob('Заметки/Снимки экрана/*.md'));assert len(notes)==1
    assert edited_text in notes[0].read_text()
    assert 'Текст отредактирован пользователем' in notes[0].read_text()
    assert next(ws.root.glob('Заметки/**/Экран.png')).read_bytes()==png
    # Repeat commands are idempotent and persisted across a ChatLog reload.
    result=CommandsMixin.command(engine,'screenshot_to_note',{'id':item['id']})
    assert len(list(ws.root.glob('Заметки/Снимки экрана/*.md')))==1
    engine.chatlog=ChatLog(root/'chat.jsonl')
    assert engine.chatlog.screenshot(item['id'])['att'][0]['note_path']==result['path']
    page.evaluate("loadScreenshotMessages()")
    page.wait_for_function("document.querySelector('[data-ss-action=note]').disabled")
    for skin in ['neusky','neudark']:
        page.evaluate('(skin)=>applyLook({skin})',skin)
        for width in [340,420,640]:
            page.set_viewport_size({'width':width,'height':850})
            for b in card.locator('button').all():
                box=b.bounding_box();assert 0<=box['x'] and box['x']+box['width']<=width
    shot=ROOT/'work/screenshot-message-card.png';page.screenshot(path=str(shot))
    card.locator('[data-ss-action="close"]').click();card.wait_for(state='detached')
    assert not engine.chatlog.page()['items']
    assert notes[0].is_file(),'Dismissal must not delete a saved note'
    engine.chatlog=ChatLog(root/'chat.jsonl')
    assert not engine.chatlog.page()['items']
    # The picker captures only a private preview. No message/OCR until confirmation.
    page.set_viewport_size({'width':640,'height':900})
    page.locator('#a-shot').click();page.locator('[data-ss-source="region"]').click()
    page.locator('.ss-region-stage canvas').wait_for()
    assert not engine.chatlog.page()['items']
    assert not any(cmd=='screenshot_attach' and args.get('draft_id') for cmd,args in calls)
    for label,value in [('Слева','100'),('Сверху','50'),('Ширина','250'),('Высота','125')]:
        page.get_by_label(label,exact=True).fill(value);page.get_by_label(label,exact=True).press('Tab')
    canvas=page.locator('.ss-region-stage canvas');box=canvas.bounding_box();scale=box['width']/700
    x=box['x']+225*scale;y=box['y']+110*scale
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+20*scale,y+10*scale);page.mouse.up()
    moved=int(page.get_by_label('Слева',exact=True).input_value());assert 118<=moved<=122,moved
    canvas.focus();page.keyboard.press('ArrowLeft')
    assert int(page.get_by_label('Слева',exact=True).input_value())==moved-1
    # Resize using the bottom-right handle, then restore the exact test crop.
    left=int(page.get_by_label('Слева',exact=True).input_value());top=int(page.get_by_label('Сверху',exact=True).input_value())
    x=box['x']+(left+250)*scale;y=box['y']+(top+125)*scale
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+25*scale,y+15*scale);page.mouse.up()
    assert int(page.get_by_label('Ширина',exact=True).input_value())>=270
    for label,value in [('Слева','100'),('Сверху','50'),('Ширина','250'),('Высота','125')]:
        page.get_by_label(label,exact=True).fill(value);page.get_by_label(label,exact=True).press('Tab')
    selection=ROOT/'work/screenshot-region-picker.png';page.screenshot(path=str(selection))
    page.locator('[data-ss-confirm="region"]').click()
    page.locator('.ss-region-stage').wait_for(state='detached')
    current=engine.chatlog.page()['items'][-1]
    captured=Image.open(ws.safe(current['att'][0]['path']))
    assert captured.size==(250,125),captured.size
    page.locator('#screenshot-messages [data-ss-action="close"]').click()
    page.locator('#screenshot-messages .ss-card').wait_for(state='detached')
    page.locator('#a-shot').click();page.locator('[data-ss-source="region"]').click()
    page.locator('.ss-region-stage canvas').wait_for();page.get_by_role('button',name='Отмена',exact=True).click()
    page.wait_for_function("!document.querySelector('.ss-region-stage')")
    assert not engine.chatlog.page()['items']
    # Visible application target is selected by stable window id, not by a guessed title.
    page.locator('#a-shot').click();page.locator('[data-window-id="42"] .ss-window-select').click()
    page.locator('[data-window-id="42"] .ss-window-capture').click()
    page.locator('#screenshot-messages .ss-card').wait_for()
    assert any(cmd=='screenshot_attach' and args.get('window_id')==42 for cmd,args in calls)
    # Saving empty text removes the icon; neither OCR disclosure nor success line returns.
    card.locator('[data-ss-action="text"]').click()
    field.fill('');editor.get_by_role('button',name='Закрыть',exact=True).click()
    editor.wait_for(state='detached')
    assert card.locator('[data-ss-action="text"]').is_hidden()
    engine.chatlog=ChatLog(root/'chat.jsonl');page.evaluate('loadScreenshotMessages()')
    page.wait_for_function("document.querySelector('#screenshot-messages [data-ss-action=text]').hidden")
    assert not card.locator('details,summary,pre').count()
    assert not errors,errors
    browser.close()
print('ALL OK: assistant message, no auto-note, PNG/text clipboard, explicit idempotent transfer, restart persistence, dismissal and layout')
