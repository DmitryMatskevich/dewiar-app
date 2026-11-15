"""While a song plays, the sheet follows the line and chord being played. Temporary SongStore only.

The current line must stay on screen: when it would go below the window, the
page scrolls — also when playback began from a card in the list, once the
sheet is what is on screen (a card list on screen is not yanked away).
Laid out like the Dewiar window (the main pane scrolls, not the page) and
checked in Chromium and, when installed, in WebKit (the engine of the Dewiar
window on macOS).
"""
import re
import sys
import tempfile
import threading
import wave
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from songs_ui_preview import SongStore, make_server

LINE_MS, LINES = 2500, 36


def fixture(store):
    row = store.create('ТЕСТ — длинная песня для прокрутки', 'Синтетические данные')
    path = store.root / row['song_id'] / 'audio' / 'silent.wav'
    path.parent.mkdir(parents=True)
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
        wav.writeframes(b'\x00\x00' * 8000 * (LINES * LINE_MS // 1000 + 4))
    phrases, chords = [], []
    for i in range(LINES):
        text = f'Строка номер {i + 1} идёт по порядку'
        start = i * LINE_MS + 200
        words = [dict(text=m.group(), char_start=m.start(), char_end=m.end(), start_ms=start + k * 380,
                      end_ms=start + k * 380 + 340, method='qwen3_forced_aligner')
                 for k, m in enumerate(re.finditer(r'\S+', text))]
        phrases.append(dict(phrase_id=f'l{i}', text=text, start_ms=start, end_ms=words[-1]['end_ms'], words=words,
                            timing_quality='word_estimated', alignment_stale=False, review_state='needs_review'))
        chords.append(dict(name=['Am', 'Dm', 'G', 'C'][i % 4], start_ms=i * LINE_MS, end_ms=(i + 1) * LINE_MS))
    store.patch(row['song_id'], dict(status='review', phrases=phrases, chords=chords,
        audio={'path': str(path.relative_to(store.root)), 'format': 'wav', 'duration_ms': LINES * LINE_MS + 4000},
        capabilities={'audio': True, 'lyrics': True, 'chords': True, 'structure': False}))
    return row['song_id']


CURRENT = """()=>{const l=document.querySelector('.song-paper-line.playing-line');
  if(!l)return null;const r=l.getBoundingClientRect();
  const tools=document.querySelector('.song-sheet-tools')?.getBoundingClientRect();
  return {top:r.top,bottom:r.bottom,height:innerHeight,tools:tools?tools.bottom:0,text:l.textContent.trim().slice(0,40)};}"""


def visible(box):
    return box and box['top'] >= box['tools'] - 2 and box['bottom'] <= box['height'] + 2


with tempfile.TemporaryDirectory(prefix='dewiar-follow-test-') as tmp, sync_playwright() as pw:
    store = SongStore(Path(tmp) / 'songs'); sid = fixture(store)
    server = make_server(store)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    checked = []
    try:
        for engine in ('chromium', 'webkit'):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as exc:
                print(f'-- {engine}: не установлен ({str(exc).splitlines()[0][:80]})')
                continue
            page = browser.new_page(viewport=dict(width=1000, height=620))
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            try:
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.add_style_tag(content='html,body{height:100%} body{overflow:hidden!important} '
                                           'main{height:100vh;overflow-y:auto!important;box-sizing:border-box}')
                page.locator(f'button[data-song-open="{sid}"]').click()
                page.wait_for_selector('.song-paper-word')
                page.locator('#song-sheet-play').click()
                page.wait_for_function("(()=>{const a=document.getElementById('song-review-audio');return a&&!a.paused&&a.currentTime>0.2})()",
                                       timeout=30000)
                # A line far below the window: the page follows it.
                page.evaluate("()=>{document.getElementById('song-review-audio').currentTime=60}")
                page.wait_for_timeout(1500)
                box = page.evaluate(CURRENT)
                assert visible(box), (engine, 'after seek', box)
                # Playing on: the line about to start is pushed below the window
                # by scrolling up; the next line change brings it back.
                page.evaluate("()=>document.querySelector('main').scrollTo(0,0)")
                page.wait_for_timeout(LINE_MS + 800)
                box = page.evaluate(CURRENT)
                assert visible(box), (engine, 'while playing', box)
                page.locator('#song-sheet-play').click()          # pause
                page.locator('#song-sheet-stop').click()
                # Played from a card: the list on screen stays where it is...
                page.evaluate("()=>document.querySelector('main').scrollTo(0,0)")
                page.evaluate("()=>SongReview.playVariant('original',false)")
                page.wait_for_function("(()=>{const a=document.getElementById('song-review-audio');return a&&!a.paused&&a.currentTime>0.2})()",
                                       timeout=30000)
                page.wait_for_timeout(LINE_MS + 500)
                assert page.evaluate("()=>document.querySelector('main').scrollTop") == 0, (engine, 'card list jumped')
                # ...but once the sheet is on screen, it follows the song.
                page.evaluate("()=>document.querySelector('.song-paper').scrollIntoView({block:'start'})")
                page.evaluate("()=>{document.getElementById('song-review-audio').currentTime=75}")
                page.wait_for_timeout(1500)
                box = page.evaluate(CURRENT)
                assert visible(box), (engine, 'card playback, sheet on screen', box)
                page.evaluate("()=>document.getElementById('song-review-audio').pause()")
                assert not errors, errors
                checked.append(engine)
                print(f'OK   {engine}: строка «{box["text"]}» видна ({box["top"]:.0f}–{box["bottom"]:.0f} из {box["height"]})')
            finally:
                browser.close()
        assert checked
        print('OK: the sheet follows the line being played in', ', '.join(checked))
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
