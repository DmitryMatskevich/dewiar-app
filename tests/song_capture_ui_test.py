"""Visible recording stop buttons using production UI and isolated fake transport."""
import json
import shutil
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from songs_ui_preview import page as preview_page

row = dict(song_id='test-mic',title='ТЕСТ запись с микрофона',revision=1,status='recording',
    stage='record',stage_label='Запись с микрофона',audio={'capture_source':'microphone','path':'test.wav'},
    capabilities={'audio':False},processing={'busy':True,'can_resume':False},phrases=[],chords=[],sources=[])
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
    ui = Path(tmp) / 'ui'
    shutil.copytree(ROOT / 'ui', ui)
    html = preview_page('fixture').decode().replace('="/studio/', '="studio/')
    start, end = html.index('const core='), html.index('</script>', html.index('const core='))
    mock = '''window.row=ROW;window.calls=[];window.handlers={};window.stopError=false;
const core={on(name,fn){handlers[name]=fn},async call(cmd,args){calls.push([cmd,args]);
 if(cmd==='songs_list'){const {audio,phrases,chords,sources,...summary}=row;return [structuredClone(summary)];}
 if(cmd==='song_get')return structuredClone(row);
 if(cmd==='song_capture'&&args.action==='stop'){
  if(window.delayStop)await new Promise(resolve=>window.resolveStop=resolve);
  if(window.stopError)throw Error('ТЕСТ: источник ещё закрывается');
  row.status='running';row.stage='lyrics';row.stage_label='Распознаём слова';row.capabilities.audio=true;
  return structuredClone(row);
 }return {};}};
'''.replace('ROW', json.dumps(row))
    (ui / 'capture.html').write_text(html[:start]+mock+html[end:])
    browser = pw.chromium.launch()
    tab = browser.new_page(viewport={'width':1100,'height':850})
    errors=[]
    tab.on('pageerror',lambda e:errors.append(str(e)))
    tab.goto((ui/'capture.html').as_uri())
    card_stop=tab.locator('#songs-list [data-song-stop-capture]')
    card_stop.wait_for()
    assert card_stop.is_visible() and card_stop.inner_text()=='Остановить запись'
    assert card_stop.locator('svg').count()==1
    assert 'сохранить звук' in card_stop.get_attribute('title')
    tab.locator('.song-card-main').click()
    detail_stop=tab.locator('#songs-detail [data-song-stop-capture]')
    assert detail_stop.is_visible()
    assert not detail_stop.evaluate('(e)=>!!e.closest("details")')
    assert '5 секунд тишины' in tab.locator('.song-recording-banner').inner_text()
    assert detail_stop.bounding_box()['y'] < tab.locator('.song-steps').bounding_box()['y']
    tab.locator('#songs-detail-close').click()
    assert card_stop.is_visible(), 'Closing editor must not hide the only stop control'
    tab.evaluate('window.delayStop=true')
    card_stop.click()
    tab.wait_for_function('!!window.resolveStop')
    assert card_stop.is_disabled()
    tab.evaluate('loadSongs()')
    assert card_stop.is_disabled(), 'Polling must not unlock a pending stop request'
    tab.evaluate('resolveStop();window.delayStop=false')
    tab.wait_for_function("!document.querySelector('[data-song-stop-capture]')")
    assert tab.evaluate("calls.filter(([cmd,a])=>cmd==='song_capture'&&a.action==='stop').length") == 1
    assert 'пакетная обработка запущена' in tab.locator('#test-message').inner_text()
    # Recording appeared in another session; stop remains available after reload.
    tab.evaluate("row.status='recording';row.stage='record';row.capabilities.audio=false;loadSongs()")
    card_stop.wait_for()
    for width in (390,760,1100):
        tab.set_viewport_size({'width':width,'height':850})
        box=card_stop.bounding_box()
        assert box['x']>=0 and box['x']+box['width']<=width, (width,box)
    # A failed stop leaves a usable retry and reports the actual error.
    tab.evaluate('window.stopError=true')
    card_stop.click()
    tab.wait_for_function("document.querySelector('#test-message').textContent.includes('источник ещё закрывается')")
    assert card_stop.is_enabled()
    tab.evaluate('window.stopError=false')
    tab.locator('.song-card-main').click()
    detail_stop.click()
    tab.wait_for_function("!document.querySelector('[data-song-stop-capture]')")
    assert not errors, errors
    browser.close()
print('OK: recording stop visible on compact card/detail, pending/retry, navigation and 390–1100 px layout')
