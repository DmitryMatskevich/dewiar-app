"""Real song cards and shared audio; temporary UI, synthetic WAV, no user data."""
import base64
import io
import json
import shutil
import sys
import tempfile
import wave
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from songs_ui_preview import page as preview_page

stream = io.BytesIO()
with wave.open(stream, 'wb') as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(8000)
    wav.writeframes(b'\0\0' * 8000 * 30)
audio = 'data:audio/wav;base64,' + base64.b64encode(stream.getvalue()).decode()
rows = [dict(song_id=str(i), title='ТЕСТ ' + str(i), revision=1, status='review', tags=['Любовь'] if i == 1 else [],
             capabilities={'audio': i != 3}, karaoke={'state':'ready','artifact':{'name':'accompaniment.wav','bytes':12000,'seconds':30}} if i == 1 else {'state':'absent'}, phrases=[], chords=[]) for i in (1, 2, 3)]
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
    ui = Path(tmp) / 'ui'
    shutil.copytree(ROOT / 'ui', ui)
    html = preview_page('fixture').decode().replace('="/studio/', '="studio/')
    start = html.index('const core=')
    end = html.index('</script>', start)
    mock = '''window.calls=[];window.rows=ROWS;window.audioData=AUDIO;
const core={on(){},async call(cmd,a){calls.push([cmd,a]);
 if(cmd==='songs_list')return structuredClone(rows);
 if(cmd==='song_get')return structuredClone(rows.find(r=>r.song_id===a.song_id));
 if(cmd==='song_transcribe')return structuredClone(rows.find(r=>r.song_id===a.song_id));
 if(cmd==='song_patch'){const r=rows.find(item=>item.song_id===a.song_id);Object.assign(r,a.changes);r.revision+=1;return structuredClone(r);}
 if(cmd==='song_resume'){const r=rows.find(item=>item.song_id===a.song_id);r.status='running';r.processing={busy:true,can_resume:false};r.stage='sources';r.stage_label='Сохраняем выбранную запись';return structuredClone(r);}
 if(cmd==='songs_instrumental_prepare_all')return {started:[{song_id:'1'}],ready:[{song_id:'2'}],already_running:[],missing_audio:[],errors:[]};
 if(cmd==='song_full_audio'){if(window.failAudio)throw Error('fixture audio error');if(window.delayAudio)await new Promise(r=>window.releaseAudio=r);return {data:audioData};}
 return {};}};
'''.replace('ROWS', json.dumps(rows)).replace('AUDIO', json.dumps(audio))
    (ui / 'test.html').write_text(html[:start] + mock + html[end:])
    browser = pw.chromium.launch()
    tab = browser.new_page(viewport={'width': 1100, 'height': 800})
    errors = []
    tab.on('pageerror', lambda e: errors.append(str(e)))
    tab.goto((ui / 'test.html').as_uri())
    play = tab.locator('[data-song-play="1"]')
    stop = tab.locator('[data-song-stop="1"]')
    play.wait_for()
    actions = tab.locator('.song-card-actions button').all_text_contents()
    assert len(actions) >= 15 and all(text == '' for text in actions)
    assert tab.locator('[data-song-open="1"]').last.get_attribute('title').startswith('Редактировать')
    assert tab.locator('[data-song-play="3"]').is_disabled()
    assert not tab.locator('[data-song-karaoke="1"]').is_disabled()
    assert tab.locator('[data-song-karaoke="2"]').is_disabled()
    assert not stop.is_visible()
    tab.locator('[data-song-card-tag="Любовь"]').click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='song_patch'&&args.song_id==='1'&&args.changes.tags.length===0)")
    tab.wait_for_function("!document.querySelector('[data-song-card-tag=\\\"Любовь\\\"]')")
    assert not tab.locator('[data-song-tag="Любовь"].on').count(), 'Card tag click must not activate the global filter'
    tab.locator('[data-song-tag-add="1"]').click()
    assert tab.locator('[data-song-id="1"] [data-song-tag-menu]').is_visible()
    tab.locator('[data-song-id="1"] [data-song-tag-option="Грусть"]').click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='song_patch'&&args.song_id==='1'&&args.changes.tags.includes('Грусть'))")
    assert tab.locator('[data-song-card-tag="Грусть"]').is_visible()
    tab.locator('[data-song-tag="Любовь"]').click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='songs_list'&&args.tags.length===1&&args.tags[0]==='Любовь')")
    tab.locator('[data-song-tag="Грусть"]').click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='songs_list'&&args.tags.length===2&&args.tags.includes('Любовь')&&args.tags.includes('Грусть'))")
    assert tab.locator('[data-song-tag="Любовь"].on').count() == 1
    assert tab.locator('[data-song-tag="Грусть"].on').count() == 1
    tab.locator('[data-song-tag="Любовь"]').click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='songs_list'&&args.tags.length===1&&args.tags[0]==='Грусть')")
    tab.locator('.song-card-main[data-song-open="1"]').click()
    tab.locator('.song-properties > summary').click()
    tab.locator('#song-start-transcribe').click()
    dialog=tab.locator('#song-transcribe-dialog')
    assert dialog.is_visible()
    assert dialog.locator('[data-song-model="whisper"]').is_checked()
    assert dialog.locator('[data-song-model="qwen_large"]').is_checked()
    assert not dialog.locator('[data-song-model="qwen"]').is_checked()
    assert not dialog.locator('[data-song-model="gigaam"]').is_checked()
    dialog.locator('#song-transcribe-cancel').click()
    assert not dialog.is_visible()
    tab.locator('#song-start-transcribe').click()
    dialog.locator('[data-song-model="qwen"]').check()
    assert not dialog.locator('[data-song-model="qwen_large"]').is_checked()
    dialog.locator('[data-song-model="qwen_large"]').check()
    assert not dialog.locator('[data-song-model="qwen"]').is_checked()
    dialog.locator('#song-transcribe-submit').click()
    tab.wait_for_function("calls.some(([cmd,a])=>cmd==='song_transcribe'&&a.models.join(',')==='whisper,qwen_large')")
    assert not dialog.is_visible()
    tab.locator('#songs-detail-close').click()
    create_button = tab.locator('#songs-new-create')
    karaoke_button = tab.locator('#songs-instrumental-all')
    assert create_button.get_attribute('title').startswith('Создать новую песню')
    assert create_button.locator('svg').count() == 1
    assert karaoke_button.get_attribute('title').startswith('Караоке:')
    assert karaoke_button.locator('svg').count() == 1
    assert create_button.locator('svg').bounding_box()['width'] >= 20
    assert karaoke_button.locator('svg').bounding_box()['width'] >= 20
    for status, label in [('batch', 'Пакетная обработка'), ('stopped', 'Остановились')]:
        chip = tab.locator(f'[data-song-status="{status}"]')
        assert chip.inner_text() == label and chip.is_visible()
    create_button.click()
    assert tab.locator('#song-create-dialog').is_visible()
    tab.locator('#song-create-cancel').click()
    tab.evaluate("rows.push({song_id:'selected-source',title:'Выбрана запись',artist:'Исполнитель',revision:1,status:'needs_input',stage:'sources',stage_label:'Запись выбрана; нажмите «Обработать пакетно», чтобы начать разбор',capabilities:{audio:false},processing:{busy:false,can_resume:true,label:'Обработать пакетно'},phrases:[],karaoke:{state:'absent'}})")
    tab.evaluate('loadSongs()')
    process=tab.locator('[data-song-process-selected="selected-source"]')
    assert process.inner_text()=='Обработать пакетно'
    assert not tab.locator('[data-song-play="selected-source"]').count()
    process.click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='song_resume'&&args.song_id==='selected-source')")
    tab.wait_for_function("rows.find(r=>r.song_id==='selected-source').status==='running'")
    assert not tab.locator('[data-song-process-selected="selected-source"]').count()
    assert tab.locator('[data-song-play="selected-source"]').is_disabled()
    # Production cards deliberately omit audio paths, phrases and candidates.
    for status in ('partial', 'failed', 'interrupted', 'cancelled'):
        tab.evaluate("status=>rows.push({song_id:status,title:'ТЕСТ '+status,revision:1,status,capabilities:{audio:true},processing:{can_resume:true,busy:false,label:'Продолжить обработку'}})", status)
    tab.evaluate('loadSongs()')
    tab.locator('[data-song-open="partial"]').first.click()
    assert tab.locator('#song-resume').is_visible()
    tab.locator('#song-resume').click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='song_resume'&&args.song_id==='partial')")
    for status in ('failed', 'interrupted', 'cancelled'):
        button=tab.locator(f'[data-song-process-selected="{status}"]')
        assert button.inner_text()=='Продолжить обработку'
        assert tab.locator(f'[data-song-play="{status}"]').is_enabled()
        button.click()
        tab.wait_for_function("sid=>calls.some(([cmd,args])=>cmd==='song_resume'&&args.song_id===sid)", arg=status)
    tab.locator('#songs-detail-close').click()
    play.click()
    tab.wait_for_function("!document.querySelector('#song-review-audio').paused")
    tab.wait_for_function("document.querySelector('[data-song-play=\"1\"]').ariaLabel.startsWith('Приостановить')")
    assert stop.is_visible() and play.get_attribute('aria-label').startswith('Приостановить')
    assert tab.evaluate('window.scrollY') == 0, 'Card playback must not jump into the editor'
    tab.locator('[data-song-karaoke="1"]').click()
    tab.wait_for_function("!document.querySelector('#song-review-audio').paused && calls.some(([cmd,args])=>cmd==='song_full_audio'&&args.variant==='instrumental')")
    tab.wait_for_function("document.querySelector('[data-song-karaoke=\"1\"]').getAttribute('aria-label').startsWith('Приостановить караоке')")
    assert tab.locator('[data-song-karaoke="1"]').get_attribute('aria-label').startswith('Приостановить караоке')
    tab.locator('[data-song-karaoke="1"]').click()
    tab.wait_for_function("document.querySelector('#song-review-audio').paused")
    play.click()
    tab.wait_for_function("!document.querySelector('#song-review-audio').paused && calls.some(([cmd,args])=>cmd==='song_full_audio'&&args.variant==='original')")
    play.click()
    tab.wait_for_function("document.querySelector('#song-review-audio').paused")
    assert tab.locator('#song-review-audio').evaluate('(a)=>a.paused')
    tab.evaluate('loadSongs()')
    tab.wait_for_function("document.querySelector('[data-song-play=\"1\"]').ariaLabel.startsWith('Продолжить')")
    play.click()
    tab.wait_for_function("!document.querySelector('#song-review-audio').paused")
    tab.locator('.song-card-actions [data-song-open="1"]').click()
    assert not tab.locator('#song-review-audio').evaluate('(a)=>a.paused'), 'Editing same card should retain audio'
    stop.click()
    assert tab.locator('#song-review-audio').evaluate('(a)=>a.paused && a.currentTime===0')
    assert not stop.is_visible()
    tab.locator('[data-song-play="2"]').click()
    tab.wait_for_function("!document.querySelector('#song-review-audio').paused")
    assert not stop.is_visible() and tab.locator('[data-song-stop="2"]').is_visible()
    tab.locator('[data-song-stop="2"]').click()
    # Cancel an in-flight audio response; it must never start playback later.
    tab.evaluate('window.delayAudio=true')
    play.click()
    tab.wait_for_function('!!window.releaseAudio')
    assert play.is_disabled() and stop.is_visible()
    stop.click()
    tab.evaluate('releaseAudio();delayAudio=false')
    tab.wait_for_function("!document.querySelector('[data-song-play=\"1\"]').disabled")
    assert tab.locator('#song-review-audio').evaluate('(a)=>a.paused')
    for width in (390, 760, 1100):
        tab.set_viewport_size({'width': width, 'height': 800})
        assert tab.locator('.song-card-actions').evaluate_all('(es)=>es.every(e=>e.getBoundingClientRect().right<=innerWidth)'), width
    tab.locator('#songs-instrumental-all').click()
    tab.wait_for_function("calls.some(([cmd])=>cmd==='songs_instrumental_prepare_all')")
    tab.locator('.song-card-main[data-song-open="1"]').click()
    assert tab.locator('.song-instrumental-stage[aria-label="Этап создания минусовки"]').is_visible()
    assert tab.locator('#song-instrumental').get_attribute('title') is None
    assert 'минусовку' in tab.locator('#song-instrumental').inner_text().lower()
    tab.locator('.song-card-main[data-song-open="3"]').click()
    tab.locator('.song-properties > summary').click()
    tab.locator('#song-start-mic-capture').wait_for()
    mic_button = tab.locator('#song-start-mic-capture')
    system_button = tab.locator('#song-start-system-capture')
    assert mic_button.get_attribute('title') == 'Запись с микрофона' and mic_button.locator('svg').count() == 1
    assert system_button.get_attribute('title') == 'Запись системного звука' and system_button.locator('svg').count() == 1
    tab.locator('#song-start-mic-capture').click()
    tab.wait_for_function("calls.some(([cmd,args])=>cmd==='song_capture'&&args.source==='microphone')")
    assert not errors, errors
    browser.close()
print('OK: icon cards, shared play/pause/stop, refresh, switching, cancellation and narrow layout')
