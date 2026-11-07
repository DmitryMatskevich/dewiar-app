"""Real fretboard + context-help + temporary SongStore. No user state or models."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from songs_ui_preview import SongStore,seed,make_server

with tempfile.TemporaryDirectory(prefix='dewiar-chord-test-') as tmp, sync_playwright() as pw:
    store=SongStore(Path(tmp)/'songs');seed(store)
    row=store.list()[0];sid=row['song_id']
    chords=[dict(name='Em',start_ms=0,end_ms=3000,voicing_fingering='0-0-0-2-2-0'),
            dict(name='Am',start_ms=3000,end_ms=6000,voicing_fingering='0-1-2-2-0-x'),
            dict(name='N',start_ms=6000,end_ms=12000)]
    store.patch(sid,dict(chords=chords))
    server=make_server(store)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    browser=pw.chromium.launch()
    page=browser.new_page(viewport=dict(width=1200,height=850))
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.add_style_tag(path=str(ROOT/'ui/assets/skins.css'))
        page.add_style_tag(path=str(ROOT/'ui/studio/theme.css'))
        page.evaluate("document.documentElement.classList.add('st-skin','st-neu')")
        page.locator(f'button[data-song-open="{sid}"]').click()
        page.wait_for_selector('.song-chord-tip')
        # Exercise the real generic-help enhancer as well as fretboard titles.
        page.evaluate('''()=>{
          window.DEWIAR_HELP={};window.DewiarDialogs={mount:()=>({addAction(){},close(){}})};
          for(const id of ['help','help-content','help-search','help-current','help-all']){
            const e=document.createElement(id==='help-search'?'input':'div');e.id=id;e.hidden=true;document.body.append(e);
          }
        }''')
        page.add_script_tag(path=str(ROOT/'ui/studio/context-help.js'))
        assert page.evaluate("typeof refreshHelpTitles")=='function'
        pop=page.locator('.song-chord-popover')
        trigger=page.locator('#song-guitar-content .song-chord-tip[data-chord-name="Em"]').first
        trigger.hover()
        assert pop.is_visible()
        a2='[data-chord-string="4"][data-chord-fret="2"]'
        b1='[data-chord-string="1"][data-chord-fret="1"]'
        pop.locator(a2).hover()
        assert pop.locator(a2).get_attribute('title')=='B · струна 5, лад 2'
        pop.locator(b1).hover()
        assert pop.locator(b1).get_attribute('title')=='C · струна 2, лад 1'
        before=store.get(sid);saved_pins=page.evaluate('JSON.stringify(localStorage)')
        pop.locator(a2).click()
        assert pop.is_visible() and pop.locator(a2).get_attribute('aria-pressed')=='false'
        assert pop.locator('[data-chord-string-toggle="4"]').inner_text()=='0'
        assert pop.locator(a2).evaluate('(e)=>e===document.activeElement')
        pop.locator(a2).press('Enter')
        assert pop.is_visible() and pop.locator(a2).get_attribute('aria-pressed')=='true'
        pop.locator(b1).click()
        assert pop.is_visible() and pop.locator(b1).get_attribute('aria-pressed')=='true'
        for _ in range(2):
            pop.locator('[data-chord-string-toggle="5"]').click();assert pop.is_visible()
        heading=pop.locator('[data-chord-name-edit]').input_value()
        page.locator('#test-banner').click()
        page.locator('#song-guitar-content .song-chord-tip[data-chord-name="Am"]').first.hover()
        assert pop.is_visible() and pop.locator('[data-chord-name-edit]').input_value()==heading
        for selector in ['[data-chord-pin]','[data-chord-next]','[data-chord-prev]',
                         '[data-chord-transpose="1"]','[data-chord-transpose="-1"]']:
            pop.locator(selector).click();assert pop.is_visible(),selector
        assert store.get(sid)==before
        assert page.evaluate('JSON.stringify(localStorage)')==saved_pins
        pop.locator('[data-chord-close]').click()
        assert pop.is_hidden() and store.get(sid)==before
        assert trigger.evaluate('(e)=>e===document.activeElement')
        trigger.press('Enter')
        assert pop.is_visible()
        assert pop.locator('[data-chord-fret]').first.evaluate('(e)=>e===document.activeElement')
        pop.locator('[data-chord-close]').click()

        trigger.hover();pop.locator(b1).click()
        assert pop.locator('.song-chord-popover-head [data-chord-apply]').count()==1
        pop.locator('[data-chord-name-edit]').fill('')
        pop.locator('[data-chord-name-edit]').press('B')
        pop.locator('[data-chord-name-edit]').press('m')
        assert pop.locator('[data-chord-description]').count()==0
        chosen_name=pop.locator('[data-chord-name-edit]').input_value()
        chosen_shape='-'.join(pop.locator('[data-chord-string-toggle]').all_inner_texts())
        assert page.evaluate('(s)=>SongSheet.inferChordName(s)',chosen_shape)=='Bm'
        # A rejected save must leave the draft and the window intact.
        def fail_save(route):
            if route.request.post_data_json.get('cmd')=='song_edit_chords':
                route.fulfill(status=400,content_type='application/json',body=json.dumps({'error':'Тест: конфликт версии'}))
            else:route.continue_()
        page.route('**/api',fail_save)
        pop.locator('[data-chord-apply]').click()
        page.wait_for_function("document.querySelector('.song-chord-popover-status')?.textContent.includes('конфликт')")
        assert pop.is_visible() and store.get(sid)==before
        page.unroute('**/api',fail_save)
        pop.locator('[data-chord-apply]').click();pop.wait_for(state='hidden')
        saved=store.get(sid)
        assert saved['chords'][0]['name']==chosen_name
        assert saved['chords'][0]['start_ms']==0
        assert all(saved['chords'][-1][key]==value for key,value in chords[-1].items())
        assert saved['chords'][0]['voicing_fingering']==chosen_shape
        page.reload();page.locator(f'button[data-song-open="{sid}"]').click()
        tip=page.locator(f'#song-guitar-content .song-chord-tip[data-chord-name="{chosen_name}"]').first
        tip.hover()
        assert '-'.join(pop.locator('[data-chord-string-toggle]').all_inner_texts())==chosen_shape
        pop.locator('[data-chord-close]').click()

        # One save from the phrase popover persists and returns to the sheet.
        page.locator('.song-paper-edit-chords').first.click()
        preview=page.locator('#song-live-edit-preview-chords .song-chord-tip').first
        preview.hover();pop.locator('[data-chord-next]').click()
        phrase_shape='-'.join(pop.locator('[data-chord-string-toggle]').all_inner_texts())
        assert phrase_shape!=chosen_shape
        pop.locator('[data-chord-apply]').click();pop.wait_for(state='hidden')
        page.locator('#song-live-editor').wait_for(state='visible')
        assert store.get(sid)['chords'][0]['voicing_fingering']==phrase_shape

        # Editing a displayed transposition must not transpose the stored name twice.
        page.locator('#song-key-up').click()
        tip=page.locator('#song-guitar-content .song-chord-tip').first
        display_name=tip.inner_text();tip.hover()
        pop.locator('[data-chord-next]').click()
        transposed_shape='-'.join(pop.locator('[data-chord-string-toggle]').all_inner_texts())
        pop.locator('[data-chord-apply]').click();pop.wait_for(state='hidden')
        assert store.get(sid)['chords'][0]['name']==chosen_name
        assert store.get(sid)['chords'][0]['voicing_key_shift']==1
        assert page.locator('#song-guitar-content .song-chord-tip').first.inner_text()==display_name
        page.locator('#song-guitar-content .song-chord-tip').first.hover()
        assert '-'.join(pop.locator('[data-chord-string-toggle]').all_inner_texts())==transposed_shape
        pop.locator('[data-chord-close]').click()

        # Small-screen light/dark geometry, opaque surface, native pitch title.
        for light in (False,True):
            page.set_viewport_size(dict(width=375,height=812))
            page.evaluate('(light)=>document.documentElement.classList.toggle("st-light",light)',light)
            page.locator('#song-guitar-content .song-chord-tip').first.hover()
            box=pop.bounding_box();assert box['x']>=0 and box['x']+box['width']<=375
            assert not pop.evaluate("e=>getComputedStyle(e).backgroundColor").startswith('rgba(')
            label_color=pop.locator('.song-chord-string').first.evaluate('e=>getComputedStyle(e).color')
            assert label_color==('rgba(29, 34, 48, 0.75)' if light else 'rgba(244, 244, 246, 0.75)'),label_color
            assert pop.locator('[data-chord-fret]').first.get_attribute('title').split(' · ')[0] in ['C','C#','D','Eb','E','F','F#','G','Ab','A','Bb','B']
            if light:
                out=ROOT/'work/chord-popover-check';out.mkdir(exist_ok=True)
                pop.screenshot(path=str(out/'editor.png'))
            pop.locator('[data-chord-close]').click()
        assert not errors,errors
        print('OK: fret pitches, repeated clicks, keyboard, cancel rollback, save failure, persistence, phrase draft, transposition and 375px themes')
    finally:
        browser.close();server.shutdown();server.server_close();thread.join(timeout=5)
