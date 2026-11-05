"""Тестовая страница: добавление своих источников без сети и реальных данных."""
import json
import shutil
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from songs_ui_preview import page as preview_page

row = {'song_id':'fixture', 'revision':1, 'title':'ТЕСТ — песня', 'artist':'', 'status':'needs_input',
       'stage':'sources', 'capabilities':{'audio':False}, 'audio':{}, 'phrases':[], 'chords':[],
       'source_candidates':[{'candidate_id':'yandex-search:test','kind':'reference','title':'Поиск в Яндекс Музыке','url':'yandexmusic://search?text=test'}]}
with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
    ui = Path(temp) / 'ui'; shutil.copytree(ROOT / 'ui', ui)
    html = preview_page('fixture').decode().replace('="/studio/', '="studio/')
    start, end = html.index('const core='), html.index('</script>', html.index('const core='))
    mock = '''window.row=ROW;window.calls=[];
const core={on(){},async call(cmd,a){calls.push([cmd,a]);
 if(cmd==='songs_list')return [structuredClone(row)];
 if(cmd==='song_get')return structuredClone(row);
 if(cmd==='song_source_add'){
   if(!a.url&&!a.lyrics)throw Error('Укажите ссылку или текст');
   row.revision++;row.source_candidates.push({candidate_id:'reference:fixture:audio',kind:'youtube',title:'Запись по ссылке пользователя',url:a.url});
   return structuredClone(row);
 }
 if(cmd==='song_source_select'){
   row.selected_source_id=a.candidate_id;
   for(const candidate of row.source_candidates)candidate.selected=candidate.candidate_id===a.candidate_id;
   return structuredClone(row);
 }
 if(cmd==='song_process_source'){row.status='running';return structuredClone(row);}
 return {};}};
'''.replace('ROW', json.dumps(row, ensure_ascii=False))
    (ui/'test.html').write_text(html[:start] + mock + html[end:], encoding='utf-8')
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={'width':900, 'height':800})
    errors=[]; page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto((ui/'test.html').as_uri())
    page.locator('[data-song-open="fixture"]').last.click()
    assert page.locator('.song-properties').evaluate('(e)=>e.open')
    page.locator('#song-add-manual-source').click()
    assert 'Укажите ссылку' in page.locator('#song-manual-source-result').inner_text()
    page.locator('#song-manual-url').fill('https://youtu.be/5ab3wVwfwr8')
    page.locator('#song-manual-lyrics').fill('Тестовый текст для сверки')
    page.locator('#song-add-manual-source').click()
    page.wait_for_function("calls.some(c=>c[0]==='song_source_add'&&c[1].url.includes('youtu.be'))")
    assert 'Источник сохранён' in page.locator('#song-manual-source-result').inner_text(), repr(page.locator('#song-manual-source-result').inner_text())
    assert page.locator('[data-song-source]').is_visible()
    assert page.locator('[data-song-yandex-capture]').is_visible()
    source_card=page.locator('.song-source').filter(has_text='Запись по ссылке пользователя')
    assert source_card.locator('[data-song-source-select]').is_visible()
    assert page.locator('.song-source-stage [data-song-source]').is_visible()
    source_card.locator('[data-song-source-select]').click()
    page.wait_for_function("calls.some(c=>c[0]==='song_source_select'&&c[1].candidate_id==='reference:fixture:audio')")
    assert 'Запись выбрана' in source_card.inner_text()
    page.locator('.song-source-stage [data-song-source]').click()
    page.wait_for_function("calls.some(c=>c[0]==='song_process_source'&&c[1].candidate_id==='reference:fixture:audio')")
    assert not errors, errors
    browser.close()
print('OK: manual source entry, validation and starting selected recording')
