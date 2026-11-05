"""Isolated live lyric/chord editing while song playback is paused.

Uses the production Songs UI with synthetic audio and mocked song APIs; no
personal library, model settings, or recordings are touched.
"""
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

audio_buffer = io.BytesIO()
with wave.open(audio_buffer, 'wb') as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(8000)
    wav.writeframes(b'\x00\x00' * 8000 * 8)
audio = 'data:audio/wav;base64,' + base64.b64encode(audio_buffer.getvalue()).decode()

phrase_text = 'Я молчу и улыбаюсь.'
words = [
    {'word_id': f'w{i}', 'phrase_id': 'phrase-1', 'text': word,
     'char_start': phrase_text.index(word), 'char_end': phrase_text.index(word) + len(word),
     'start_ms': i * 1000, 'end_ms': (i + 1) * 1000, 'quality': 'aligned'}
    for i, word in enumerate(['Я', 'молчу', 'и', 'улыбаюсь.'])
]
row = {
    'song_id': 'live-editor-fixture', 'title': 'Синтетическая песня — тест редактора',
    'artist': 'Тестовые данные', 'revision': 1, 'status': 'review',
    'capabilities': {'audio': True, 'lyrics': True, 'chords': True},
    'phrases': [{'phrase_id': 'phrase-1', 'start_ms': 0, 'end_ms': 8000,
                 'text': phrase_text, 'words': [], 'review_state': 'needs_review', 'note': ''}],
    'chords': [
        {'name': 'Em', 'start_ms': 500, 'end_ms': 1000, 'method': 'test'},
        {'name': 'Am', 'start_ms': 0, 'end_ms': 2500,
         'anchor': {'phrase_id': 'phrase-1', 'grapheme_index': 0}, 'method': 'test'},
        {'name': 'E7', 'start_ms': 2500, 'end_ms': 6000,
         'anchor': {'phrase_id': 'phrase-1', 'grapheme_index': 10}, 'method': 'test'},
    ],
    'sections': [],
    'guitar_sheet': [{'text': '', 'unplaced_chords': [{'name': 'Em', 'start_ms': 0, 'end_ms': 1000}]},
                     {'phrase_id': 'phrase-1', 'text': phrase_text, 'start_ms': 1000, 'end_ms': 8000,
                      'display_words': [dict(word, quality='estimated_phrase') for word in words],
                      'chords': [{'name': 'Am', 'char_index': 0, 'start_ms': 0, 'end_ms': 2500},
                                 {'name': 'E7', 'char_index': 10, 'start_ms': 2500, 'end_ms': 6000}]}],
}

with tempfile.TemporaryDirectory(prefix='dewiar-live-editor-ui-') as temp, sync_playwright() as pw:
    ui = Path(temp) / 'ui'
    shutil.copytree(ROOT / 'ui', ui)
    html = preview_page('fixture').decode().replace('="/studio/', '="studio/')
    start = html.index('const core=')
    end = html.index('</script>', start)
    mock = '''window.calls=[];window.row=ROW;window.audioData=AUDIO;
const core={on(){},async call(cmd,args){calls.push([cmd,args]);
 if(cmd==='songs_list')return [structuredClone(row)];
 if(cmd==='song_get')return structuredClone(row);
 if(cmd==='song_full_audio')return {data:audioData};
 if(cmd==='song_edit_phrase'){
   const p=row.phrases.find(x=>x.phrase_id===args.phrase_id);
   if(args.word_start!==undefined){let w=p.words.find(x=>x.char_start===args.word_start);if(!w){const token=[...p.text.matchAll(/\\S+/g)].find(x=>x.index===args.word_start);w={text:token[0],char_start:token.index,char_end:token.index+token[0].length,start_ms:args.word_start_ms,end_ms:args.word_end_ms,method:'manual_word_edit_estimated'};p.words.push(w);}const from=w.char_start,to=w.char_end,delta=args.text.length-(to-from);p.text=p.text.slice(0,from)+args.text+p.text.slice(to);p.words.forEach(x=>{if(x===w){x.text=args.text;x.char_end=from+args.text.length;}else if(x.char_start>=to){x.char_start+=delta;x.char_end+=delta;}});}
   else {p.text=args.text;p.words=[];p.alignment_stale=true;}
   p.review_state=args.reviewed?'verified':'needs_review';row.revision++;return structuredClone(row);
 }
 if(cmd==='song_edit_chords'){row.chords=args.chords;row.revision++;return structuredClone(row);}
 throw Error('Unexpected command: '+cmd);
}};
'''.replace('ROW', json.dumps(row, ensure_ascii=False)).replace('AUDIO', json.dumps(audio))
    (ui / 'test.html').write_text(html[:start] + mock + html[end:])
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={'width': 1200, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto((ui / 'test.html').as_uri())
    page.locator('[data-song-open="live-editor-fixture"]').last.wait_for()
    page.locator('[data-song-open="live-editor-fixture"]').last.click()
    page.wait_for_function("document.querySelector('.song-paper')?.dataset.songId==='live-editor-fixture'")
    assert page.locator('#song-edit-current').count() == 0
    for button_selector, text_selector in [
        ('.song-paper-edit-chords', '.song-paper-chords'),
        ('.song-paper-edit-lyrics', '.song-paper-lyrics'),
    ]:
        button_box = page.locator(button_selector).last.bounding_box()
        text_box = page.locator(text_selector).last.bounding_box()
        assert button_box['x'] + button_box['width'] <= text_box['x']

    page.locator('#song-sheet-play').click()
    page.wait_for_function("!document.querySelector('#song-review-audio').paused")
    player = page.locator('#song-review-audio')
    def cue_at(seconds):
        player.evaluate("(a,t)=>{a.currentTime=t;a.dispatchEvent(new Event('timeupdate'))}", seconds)
        page.wait_for_function("document.querySelector('.song-paper-instrumental.playing-instrumental')")
        return page.locator('.song-paper-cue-part.countdown-lit').inner_text()
    assert cue_at(0.1) == 'Всту'
    assert cue_at(0.45) == 'пле'
    assert cue_at(0.8) == 'ние'
    assert not page.locator('.song-paper-countdown').count()
    assert not page.locator('.song-paper-instrumental').inner_text().startswith('До ')
    player.evaluate("a=>{a.currentTime=3.2;a.dispatchEvent(new Event('timeupdate'))}")
    page.wait_for_function("document.querySelector('.song-paper-word.current-word')?.textContent==='улыбаюсь.'")
    page.locator('.song-paper-edit-lyrics[data-song-edit-phrase="phrase-1"]').click()
    assert page.locator('#song-live-editor').is_visible()
    assert page.locator('#song-review-audio').evaluate('(a)=>a.paused')
    assert page.locator('#song-live-editor').is_visible()
    assert page.locator('#song-live-edit-mode').input_value() == 'phrase', page.locator('#song-live-edit-mode').input_value()
    assert page.locator('#song-live-edit-text').input_value() == phrase_text
    assert page.locator('#song-live-edit-save').evaluate("e=>e.closest('.song-live-editor-head')!==null")

    page.locator('#song-live-edit-text').fill('Я тихо смеюсь.')
    assert page.locator('#song-live-edit-preview').inner_text() == 'Я тихо смеюсь.'
    page.locator('#song-live-edit-save').click()
    page.wait_for_function("calls.some(([cmd])=>cmd==='song_edit_phrase')")
    assert page.locator('#song-review-audio').evaluate('(a)=>a.paused')
    assert 'времена слов нужно проверить заново' in page.locator('#song-live-edit-status').inner_text()
    assert page.evaluate("row.phrases[0].text") == 'Я тихо смеюсь.'
    assert page.evaluate("calls.find(([cmd])=>cmd==='song_edit_phrase')[1].text") == 'Я тихо смеюсь.'

    page.locator('.song-paper-edit-chords[data-song-edit-phrase="phrase-1"]').last.click()
    assert page.locator('#song-live-edit-mode').input_value() == 'chords'
    assert page.locator('#song-live-text-field').is_hidden()
    assert page.locator('[data-live-chord-name]').count() == 3
    page.locator('[data-live-chord="2"] [data-live-chord-name]').fill('G7')
    page.locator('#song-live-edit-save').click()
    page.wait_for_function("calls.some(([cmd])=>cmd==='song_edit_chords')")
    assert 'Аккорды фразы сохранены' in page.locator('#song-live-edit-status').inner_text()
    assert page.evaluate("row.chords.find(ch=>ch.start_ms===2500).name") == 'G7'
    assert not errors, errors
    browser.close()

print('OK: current word highlight, inline phrase/chord editing, pause-on-edit and independent chord save')
