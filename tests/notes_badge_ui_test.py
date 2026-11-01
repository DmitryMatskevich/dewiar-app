"""HUD note indicator: new note, OCR deduplication, opening/reading, failures and layout."""
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as folder,sync_playwright() as pw:
    ui=Path(folder)/'ui';shutil.copytree(ROOT/'ui',ui)
    (ui/'assets/dewiar.js').write_text('''window.opens=[];window.calls=[];window.failOpen=false;window.failFile=false;
      window.api=()=>({async open_settings(...args){if(failOpen)throw new Error('Нет окна');opens.push(args);return true;}});
      window.core={_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},async call(cmd,args){calls.push([cmd,args]);if(cmd==='ws_open'&&failFile)throw new Error('Не открылось');return {}}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));''')
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':380,'height':650})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto((ui/'hud.html').as_uri())
    page.wait_for_function("typeof markNewNote==='function'")
    dot=page.locator('#b-notes-dot');button=page.locator('#b-notes')
    assert dot.is_hidden()
    note={'kind':'note','path':'Заметки/Снимки экрана/Снимок 1.md','title':'Снимок 1','text':'Снимок сохранён'}
    page.evaluate("n=>fire('note_saved',n)",note)
    assert dot.is_visible()
    assert button.evaluate("e=>e.classList.contains('has-new-notes')")
    assert 'Снимок 1' in button.get_attribute('title')
    assert 'Новых заметок: 1' in button.get_attribute('aria-label')
    # OCR updates the already-created note, rather than adding another unread item.
    page.evaluate("n=>fire('note_saved',{...n,text:'OCR готов'})",note)
    assert page.evaluate('unreadNotes.size')==1
    page.evaluate("fire('note_saved',{kind:'fact',path:'Память/Обо мне.md',title:'Факт',text:'Факт'})")
    assert page.evaluate('unreadNotes.size')==1
    # Opening failure must not silently consume the indicator.
    page.evaluate('failOpen=true');button.click(force=True)
    assert dot.is_visible()
    page.evaluate('failOpen=false');button.click(force=True)
    assert page.evaluate('opens.at(-1)')==['notes','&note='+quote(note['path'],safe="~()*!.'-")]
    assert dot.is_visible(),'Wait for Studio to actually load the note'
    page.evaluate("n=>fire('notes_seen',{path:n.path})",note)
    page.wait_for_function('unreadNotes.size===0')
    assert dot.is_hidden()
    assert not button.evaluate("e=>e.classList.contains('has-new-notes')")
    page.evaluate("n=>fire('note_saved',n)",note)
    assert dot.is_hidden(),'Updating an acknowledged note must not create another new-note signal'
    for n in [2,3]:
        page.evaluate("n=>fire('note_saved',{kind:'note',path:`Заметки/Снимок ${n}.md`,title:`Снимок ${n}`,text:'Готово'})",n)
    assert page.evaluate('unreadNotes.size')==2
    button.click(force=True);page.evaluate("fire('notes_seen',{path:'Заметки/Снимок 3.md'})");page.wait_for_function('unreadNotes.size===1')
    assert dot.is_visible() and 'Снимок 2' in button.get_attribute('title')
    page.evaluate("fire('notes_seen',{path:'Заметки/Снимок 2.md'})")
    assert dot.is_hidden()
    # Existing notification's Open action acknowledges only a successful file opening.
    page.evaluate("fire('note_saved',{kind:'note',path:'Заметки/Снимок 4.md',title:'Снимок 4',text:'Готово'});failFile=true")
    page.locator('#rem-undo').click(force=True)
    assert dot.is_visible()
    page.evaluate('failFile=false');page.locator('#rem-undo').click(force=True)
    page.wait_for_function('unreadNotes.size===0')
    assert dot.is_hidden()
    page.evaluate("fire('note_saved',{kind:'note',path:'Заметки/Снимок 5.md',title:'Снимок 5',text:'Готово'})")
    for skin in ['neusky','neudark']:
        page.evaluate('(skin)=>applyLook({skin})',skin)
        for width in [340,640]:
            page.set_viewport_size({'width':width,'height':650})
            rect=button.bounding_box();mark=dot.bounding_box()
            assert rect['x']<=mark['x'] and mark['x']+mark['width']<=rect['x']+rect['width']
            assert rect['y']<=mark['y'] and mark['y']+mark['height']<=rect['y']+rect['height']
    page.set_viewport_size({'width':380,'height':650});page.evaluate("applyLook({skin:'neusky'});showCap('','')")
    shot=ROOT/'work/screen-document/notes-badge.png';shot.parent.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shot))
    assert not errors,errors
    browser.close()
print('ALL OK: note dot, informative title, OCR deduplication, read/open acknowledgement, failure preservation and layout')
