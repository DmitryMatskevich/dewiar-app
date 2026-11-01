"""Completed transcription exposes saved results, never source-video choices or re-download/export."""
import os
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    ui=Path(folder)/'ui';shutil.copytree(ROOT/'ui',ui)
    (ui/'assets/dewiar.js').write_text('''window.calls=[];window.opens=[];window.api=()=>({open_settings:(...args)=>opens.push(args)});window.core={on(){return this},async call(cmd,args){calls.push([cmd,args]);return {}}};''')
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':420,'height':650})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto((ui/'hud.html').as_uri())
    page.wait_for_function("typeof renderDone==='function'")
    page.evaluate("""() => {applyLook({skin:'neusky'});window.fixture={id:'abc12345',workflow:'media-transcription',status:'done',title:'Извлечение текста из видео',outputs:[
      {path:'outputs/clip/transcript.txt',title:'Исходная расшифровка'},
      {path:'outputs/clip/transcript.clean.md',title:'Чистовой текст'},
      {path:'outputs/clip/transcript.summary.md',title:'Резюме ролика'},
      {path:'outputs/clip/source.mp4',title:'Исходный ролик'}]};
      tasks[fixture.id]=fixture;doneAt[fixture.id]=Date.now();renderTask();} """)
    card=page.locator('#task')
    assert card.is_visible()
    assert 'Готовый текст сохранён' in card.inner_text()
    assert card.locator('[data-a="ready"]').inner_text()=='Открыть резюме'
    assert card.locator('[data-a="file"],[data-a="html"],[data-a="pdf"]').count()==0
    assert all(word not in card.inner_text() for word in ['Посмотреть','Скачать','PDF'])
    assert page.evaluate('calls.length')==0,'Rendering a completed task must not execute any tool'
    card.locator('[data-a="ready"]').click()
    page.wait_for_function('calls.length===1')
    assert page.evaluate('calls[0]')==['task_open_result',{'id':'abc12345','path':'outputs/clip/transcript.summary.md'}]
    card.locator('[data-a="results"]').click()
    page.wait_for_function('opens.length===1')
    assert page.evaluate('opens[0]')==['tasks','&task=abc12345']
    assert not page.evaluate("calls.some(([cmd])=>['task_export','media_transcribe','start_task'].includes(cmd))")
    shot=ROOT/'work/media-routing/completed-result.png';shot.parent.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shot))
    page.evaluate("fixture.outputs=[{path:'outputs/clip/transcript.txt',title:'Распознанный текст'}];renderTask()")
    assert card.locator('[data-a="ready"]').inner_text()=='Открыть расшифровку'
    page.evaluate("fixture.outputs=[];renderTask()")
    assert card.locator('[data-a="ready"]').count()==0
    assert card.locator('[data-a="results"]').inner_text()=='Открыть задачу'
    # Existing HTML/code tasks keep their export tools.
    page.evaluate("delete fixture.workflow;fixture.title='Готовая страница';fixture.outputs=[{path:'outputs/site.html'}];renderTask()")
    assert card.locator('[data-a="html"]').is_visible()
    assert card.locator('[data-a="pdf"]').is_visible()
    assert not errors,errors
    browser.close()
print('ALL OK: completed transcript opens saved text, all files, no re-download/export, explicit labels, no-output case; other task exports preserved')
