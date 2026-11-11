"""Real chord editor + temporary store; no user library, models or microphone."""
import copy
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from songs_ui_preview import SongStore, make_server
from songs.arrangement import preview_chords
from songs.public import public_song


with tempfile.TemporaryDirectory(prefix='dewiar-chord-editor-') as temp, sync_playwright() as pw:
    store = SongStore(Path(temp) / 'songs')
    row = store.create('ТЕСТ — аккорды и слова')
    sid = row['song_id']
    text = 'Было дело ночью, но не так уж поздно.'
    tokens = text.split()
    words, cursor = [], 0
    for i, token in enumerate(tokens):
        start = text.index(token, cursor)
        words.append(dict(text=token, char_start=start, char_end=start+len(token),
                          start_ms=i*1000, end_ms=(i+1)*1000))
        cursor = start + len(token)
    events = [dict(name=name, start_ms=start, end_ms=end, verified=True,
                   anchor=dict(kind='manual', phrase_id='p', grapheme_index=words[word]['char_start']))
              for name, start, end, word in [('Am', 43, 1408, 0), ('G7', 1408, 2560, 1),
                                            ('F', 2560, 3456, 2), ('E', 3456, 4309, 3),
                                            ('Am', 4309, 8000, 4)]]
    row = store.patch(sid, dict(phrases=[dict(phrase_id='p', text=text, start_ms=0,
                                            end_ms=8000, words=words)], chords=events,
                               audio=dict(duration_ms=8000), status='review',
                               capabilities=dict(audio=True, lyrics=True, chords=True)))
    before = copy.deepcopy(store.get(sid))
    positions = preview_chords(store, dict(song_id=sid, chords=events))['positions']
    assert [positions[i]['char_index'] for i in range(5)] == [0, 5, 10, 17, 20]
    assert store.get(sid) == before, 'Preview must not save drafts'
    server = make_server(store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = pw.chromium.launch()
    page = browser.new_page(viewport=dict(width=1200, height=950))
    page.set_default_timeout(8000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.add_style_tag(path=str(ROOT/'ui/assets/skins.css'))
        page.add_style_tag(path=str(ROOT/'ui/studio/theme.css'))
        page.evaluate("document.documentElement.classList.add('st-skin','st-light','st-neu')")
        # Metadata controls inside the card must not trigger its open action.
        page.locator('[data-song-tag-add]').click()
        tag_input = page.locator('[data-song-tag-custom-form] input')
        tag_input.click()
        tag_input.fill('Репетиция')
        tag_input.press('Enter')
        page.locator('[data-song-card-tag="Репетиция"]').wait_for()
        assert page.locator('#songs-detail').is_hidden()
        page.locator('[data-song-rating-value="4"]').click()
        page.wait_for_function("document.querySelector('[data-song-rating-value=\"4\"]')?.getAttribute('aria-pressed')==='true'")
        page.reload()
        page.locator('[data-song-card-tag="Репетиция"]').wait_for()
        assert page.locator('[data-song-rating-value="4"]').get_attribute('aria-pressed') == 'true'
        assert store.get(sid)['rating'] == 4 and 'Репетиция' in store.get(sid)['tags']
        before = copy.deepcopy(store.get(sid))
        page.locator(f'button[data-song-open="{sid}"]').click()
        names = page.locator('#song-guitar-content .song-chord-tip')
        names.first.wait_for()
        assert names.all_text_contents() == ['Am', 'G7', 'F', 'E', 'Am'], names.all_text_contents()
        page.locator('.song-paper-edit-chords').first.click()
        ctx = page.locator('[data-live-chord="1"] [data-live-chord-context]')
        page.wait_for_function("document.querySelector('[data-live-chord=\"1\"] mark')?.textContent==='дело '")
        assert ctx.locator('b').inner_text() == 'G7'
        assert ctx.text_content().replace('G7', '', 1) == text
        assert page.locator('[data-live-chord-fingering]').count() == 0
        page.locator('[data-live-chord="1"] [data-live-chord-time]').fill('')
        page.locator('#song-live-edit-save').click()
        page.wait_for_function("document.querySelector('#song-live-edit-status').textContent.includes('Проверьте время')")
        assert store.get(sid) == before
        # Rename, move past the old end, add/remove another draft: nothing is lost.
        page.locator('[data-live-chord="1"] [data-live-chord-name]').fill('Dm7')
        page.locator('[data-live-chord="1"] [data-live-chord-time]').fill('6.2')
        page.wait_for_function("document.querySelector('[data-live-chord=\"1\"] mark')?.textContent==='уж поздно.'")
        assert ctx.locator('b').inner_text() == 'Dm7'
        page.locator('#song-live-chord-add').click()
        page.locator('[data-live-chord-remove="5"]').click()
        assert page.locator('[data-live-chord="1"] [data-live-chord-name]').input_value() == 'Dm7'
        assert page.locator('[data-live-chord="1"] [data-live-chord-time]').input_value() == '6.200'
        assert store.get(sid) == before
        page.locator('#song-live-edit-save').click()
        page.wait_for_function("document.querySelector('#song-live-edit-status').textContent.includes('Аккорды фразы сохранены')")
        saved = store.get(sid)
        moved = next(c for c in saved['chords'] if c['name'] == 'Dm7')
        assert moved['start_ms'] == 6200 and not moved.get('anchor')
        assert 'original_start_ms' not in moved
        assert names.all_text_contents() == ['Am', 'F', 'E', 'Am', 'Dm7']
        page.reload()
        page.add_style_tag(path=str(ROOT/'ui/assets/skins.css'))
        page.add_style_tag(path=str(ROOT/'ui/studio/theme.css'))
        page.evaluate("document.documentElement.classList.add('st-skin','st-neu')")
        page.locator(f'button[data-song-open="{sid}"]').click()
        names.first.wait_for()
        assert names.all_text_contents() == ['Am', 'F', 'E', 'Am', 'Dm7']
        # Editor uses the same selected key. A rename converts back only once.
        page.locator('#song-key-up').click()
        page.locator('.song-paper-edit-chords').first.click()
        assert page.locator('[data-live-chord-name]').first.input_value() == 'Bbm'
        page.locator('[data-live-chord-name]').first.fill('B7')
        page.locator('#song-live-edit-save').click()
        page.wait_for_function("document.querySelector('#song-live-edit-status').textContent.includes('Аккорды фразы сохранены')")
        assert store.get(sid)['chords'][0]['name'] == 'Bb7'
        assert names.first.inner_text() == 'B7'
        # Layout/copy preserve every change and every word in all widths/themes.
        for light in (True, False):
            page.evaluate('(light)=>document.documentElement.classList.toggle("st-light",light)', light)
            color = page.locator('[data-live-chord-context] mark').first.evaluate('(e)=>getComputedStyle(e).color')
            assert color == ('rgb(29, 34, 48)' if light else 'rgb(244, 244, 246)'), color
            for width in (375, 800, 1440):
                page.set_viewport_size(dict(width=width, height=950))
                page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                assert names.all_text_contents() == ['B7', 'Gb', 'F', 'Bbm', 'Ebm7']
                assert page.evaluate('''()=>[...document.querySelectorAll('[data-live-chord-context]')].every(e=>{
                    const b=e.getBoundingClientRect();return b.width>0&&b.left>=0&&b.right<=innerWidth;
                })''')
                actual = page.locator('.song-paper-lyrics').all_text_contents()
                assert ' '.join(' '.join(actual).split()) == text
                assert page.evaluate('''()=>{
                    const copy=document.querySelector('#song-copy-preview').value;
                    return [...document.querySelectorAll('.song-paper-chords')].every(e=>copy.includes(e.textContent));
                }''')
        evidence = ROOT/'work/chord-editor-check'
        evidence.mkdir(parents=True, exist_ok=True)
        page.evaluate("document.documentElement.classList.add('st-light')")
        page.locator('#song-live-editor').screenshot(path=str(evidence/'editor.png'))
        assert not errors, errors
        assert SongStore(store.root).get(sid)['chords'] == store.get(sid)['chords']
        print('Chord context, time edits, reload, transposition, copy, 3 widths, 2 themes: OK')
    except Exception:
        print('UI errors:', errors)
        print('Contexts:', page.locator('[data-live-chord-context]').all_text_contents())
        print('Status:', page.locator('#song-live-edit-status').text_content())
        print('Context titles:', page.locator('[data-live-chord-context]').evaluate_all('(es)=>es.map(e=>e.title)'))
        raise
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
