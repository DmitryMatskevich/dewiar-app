"""«Правка строк» on the real sheet: blank lines, labels, line breaks. Temporary SongStore only."""
import re
import sys
import tempfile
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from songs_ui_preview import SongStore, seed, make_server

TEXTS = ['Здесь проверяем первую строку.', 'И сохраняем каждую поправку.',
         'Здесь проверяем первую строку.', 'И сохраняем каждую поправку.']


def timed(pid, text, start):
    words, t = [], start
    for m in re.finditer(r'\S+', text):
        words.append(dict(text=m.group(), char_start=m.start(), char_end=m.end(), start_ms=t, end_ms=t + 400,
                          method='qwen3_forced_aligner'))
        t += 450
    return dict(phrase_id=pid, text=text, start_ms=start, end_ms=t, words=words, timing_quality='word_estimated',
                alignment_stale=False, review_state='needs_review', note='')


with tempfile.TemporaryDirectory(prefix='dewiar-layout-test-') as tmp, sync_playwright() as pw:
    store = SongStore(Path(tmp) / 'songs'); seed(store)
    sid = store.list()[0]['song_id']
    publication = ('Здесь проверяем первую строку.\nИ сохраняем каждую поправку.\n\n'
                   'Припев:\nЗдесь проверяем первую строку.\nИ сохраняем каждую поправку.')
    from songs.reference_fragments import lyrics_with_labels
    text, labels = lyrics_with_labels(publication)
    store.patch(sid, dict(status='review', active_run_id=None,
                          phrases=[timed(f'test-{i}', t, i * 2800) for i, t in enumerate(TEXTS)],
                          chords=[dict(name='Am', start_ms=0, end_ms=6000), dict(name='Dm', start_ms=6000, end_ms=12000)],
                          text_reference=dict(state='compared', references=[dict(url='https://example.org/song',
                              plain_lyrics=text, labels=labels, text_confirmed=True)], candidates=[])))
    server = make_server(store)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    browser = pw.chromium.launch()
    page = browser.new_page(viewport=dict(width=1200, height=900))
    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/')
        page.locator(f'button[data-song-open="{sid}"]').click()
        page.wait_for_selector('.song-paper')
        paper = page.locator('.song-paper')
        lyrics = lambda: paper.locator('.song-paper-lyrics').all_inner_texts()
        blank_before = lambda: paper.locator('.song-paper-space [data-song-layout]').count()  # blank lines before sung lines
        # The publication's quatrains and its «Припев» label, before any edit.
        assert lyrics() == TEXTS, lyrics()
        assert blank_before() == 1
        assert paper.locator('.song-paper-label').all_inner_texts() == ['Припев']
        assert paper.locator('.song-layout-tools').first.is_hidden()       # only in «Правка строк»
        toggle = page.locator('#song-layout-edit')
        toggle.click()
        assert toggle.get_attribute('aria-pressed') == 'true' and paper.get_attribute('class').count('is-layout-editing')
        assert page.locator('.song-layout-hint').is_visible()
        # «+» before the second line: a blank line, saved on the card.
        paper.locator('[data-song-layout="break-on"][data-layout-key="test-1:0"]').click()
        page.wait_for_function("document.querySelectorAll('.song-paper-space [data-song-layout]').length===2")
        assert store.get(sid)['sheet_layout']['breaks'] == {'test-1:0': True}
        assert page.locator('#song-layout-edit').get_attribute('aria-pressed') == 'true'
        # «−» on it removes it again.
        page.locator('.song-paper-space [data-layout-key="test-1:0"]').click()
        page.wait_for_function("document.querySelectorAll('.song-paper-space [data-song-layout]').length===1")
        assert store.get(sid)['sheet_layout']['breaks'] == {'test-1:0': False}
        # A label of the user's own over the last line.
        page.locator('[data-song-layout="note"][data-layout-key="test-3:0"]').click()
        page.locator('.song-layout-note [data-note-preset="×2"]').click()
        page.wait_for_function("[...document.querySelectorAll('.song-paper-label')].some(e=>e.textContent.includes('×2'))")
        assert store.get(sid)['sheet_layout']['notes'] == {'test-3:0': '×2'}
        # The publication's label is hidden on «−».
        page.locator('.song-paper-label [data-song-layout="note-remove"][data-layout-key="test-2:0"]').click()
        page.wait_for_function("![...document.querySelectorAll('.song-paper-label')].some(e=>e.textContent.includes('Припев'))")
        assert store.get(sid)['sheet_layout']['notes']['test-2:0'] is False
        # A click on a word starts a new line with it; «⤒» joins it back.
        page.locator('.song-paper-word', has_text='сохраняем').first.click()
        page.wait_for_function("[...document.querySelectorAll('.song-paper-lyrics')].some(e=>e.textContent.trim()==='И')")
        key = next(iter(store.get(sid)['sheet_layout']['cuts']))
        assert key == 'test-1:2', key
        page.locator(f'[data-song-layout="join"][data-layout-key="{key}"]').click()
        page.wait_for_function("![...document.querySelectorAll('.song-paper-lyrics')].some(e=>e.textContent.trim()==='И')")
        assert lyrics() == TEXTS, lyrics()
        out = ROOT / 'work/layout-edit-check'; out.mkdir(parents=True, exist_ok=True)
        paper.screenshot(path=str(out / 'layout-editing.png'))
        toggle.click()
        assert paper.locator('.song-layout-tools').first.is_hidden()
        assert not errors, errors
        print('OK: publication quatrains and label, blank line add/remove, own label, hidden label, split and join')
    finally:
        browser.close(); server.shutdown(); server.server_close(); thread.join(timeout=5)
