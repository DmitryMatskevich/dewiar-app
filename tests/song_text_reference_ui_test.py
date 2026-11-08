"""Text-source stage in the real UI, isolated temporary song store."""
import sys
import tempfile
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from songs_ui_preview import SongStore,seed,make_server

with tempfile.TemporaryDirectory(prefix='dewiar-reference-ui-') as tmp, sync_playwright() as pw:
    store=SongStore(Path(tmp)/'songs');seed(store);row=store.list()[0];sid=row['song_id']
    store.patch(sid,{'audio':{'path':'fixture.wav'},'capabilities':{'lyrics':True,'audio':False}})
    server=make_server(store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1200,'height':900})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.locator(f'button[data-song-open="{sid}"]').click()
        page.wait_for_selector('.song-step')
        assert page.locator('.song-step').count()==9
        assert page.locator('.song-step-unused[data-song-reference-stage]').count()==1
        assert page.locator('.song-step').nth(3).inner_text().startswith('4. Распознавание слов с аудио')
        assert page.get_by_role('button',name='Найти и сверить текст',exact=True).is_visible()
        ref={'candidate_id':'text-reference:test','kind':'lyrics','title':'ТЕСТ — стихотворение',
             'url':'https://example.org/poem','plain_lyrics':'Тестовый текст',
             'text_confirmation':{'hits':[{'anchor':f'Тестовая достоверная фраза номер {i}'} for i in range(3)]}}
        store.patch(sid,{'text_reference':{'state':'compared','references':[ref],'candidates':[],
                    'chord_sources':[dict(ref,url='https://example.org/chords')],'applied':0}})
        page.reload();page.locator(f'button[data-song-open="{sid}"]').click()
        page.wait_for_selector('.song-text-reference summary')
        assert page.locator('.song-step').count()==9
        assert '5. Сверка с источником текста' in page.locator('.song-step').nth(4).inner_text()
        panel=page.locator('.song-text-reference')
        panel.locator('.song-reference-diff > summary').first.click()
        assert panel.locator('li').count()==3
        assert panel.get_by_role('link',name='Открыть источник текста').get_attribute('href')==ref['url']
        page.locator('.song-properties > summary').click()
        assert page.locator('[data-confirmed-source="lyrics"] a').get_attribute('href')==ref['url']
        assert page.locator('[data-confirmed-source="chords"] a').get_attribute('href')=='https://example.org/chords'
        called=[]
        def retry(route):
            body=route.request.post_data_json
            if body.get('cmd')=='song_rerun_step':
                called.append(body['args']);route.fulfill(json={'result':store.get(sid)})
            else:route.continue_()
        page.route('**/api',retry)
        panel.get_by_role('button',name='Повторить сверку',exact=True).click()
        page.wait_for_timeout(200)
        assert called==[{'song_id':sid,'step':'text_reference'}]
        page.locator('[data-song-reference-stage] b').click()
        page.wait_for_timeout(200)
        assert len(called)==2
        page.set_viewport_size({'width':375,'height':812})
        panel=page.locator('.song-text-reference');panel.scroll_into_view_if_needed()
        assert panel.bounding_box()['width']<=375
        out=ROOT/'work/text-reference-check';out.mkdir(parents=True,exist_ok=True)
        panel.screenshot(path=str(out/'reference-stage.png'))
        store.patch(sid,{'text_reference':{'state':'not_found','references':[]}})
        page.reload();page.locator(f'button[data-song-open="{sid}"]').click()
        page.wait_for_selector('.song-step')
        assert page.locator('.song-step').count()==9
        assert page.locator('[data-song-reference-stage]').get_attribute('aria-disabled')=='false'
        assert 'не найден' in page.locator('.song-text-reference').inner_text()
        store.patch(sid,{'text_reference':{'state':'search_error','references':[],
            'errors':[{'error':'yandex: требуется проверка в браузере'}]}})
        page.reload();page.locator(f'button[data-song-open="{sid}"]').click()
        panel=page.locator('.song-text-reference')
        panel.get_by_text('Почему поиск не завершён',exact=True).click()
        assert panel.get_by_text('yandex: требуется проверка в браузере',exact=True).is_visible()
        assert not errors,errors
        print('OK: permanent dimmed fifth stage, click/ retry, confirmed source groups and narrow screen')
    finally:
        browser.close();server.shutdown();server.server_close();thread.join(timeout=5)
