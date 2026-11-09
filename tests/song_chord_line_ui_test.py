"""A chord held into the next line is lit on the line being sung (playback)."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def words(text, times, pid):
    import re
    tokens = list(re.finditer(r'\S+', text))
    return [dict(word_id=f'{pid}-{i}', phrase_id=pid, char_start=t.start(), char_end=t.end(), start_ms=a, end_ms=b,
                 quality='aligned') for i, (t, (a, b)) in enumerate(zip(tokens, times))]


def main():
    one = 'Мы с тобой. Опять в одной лодке, знаешь,'
    two = 'я очень очень очень верю в нас.'
    row = {'song_id': 'held-chord', 'title': 'ТЕСТ', 'guitar_sheet': [
        {'phrase_id': 'a', 'text': one, 'stanza_start': True,
         'chords': [dict(name='F#7', char_index=0, start_ms=0, end_ms=2000),
                    dict(name='Bm', char_index=one.index('лодке'), start_ms=2000, end_ms=4500)],
         'display_words': words(one, [(0, 300), (300, 500), (500, 1000), (1000, 1300), (1300, 1400), (1400, 1900),
                                      (2000, 2600), (2600, 3200)], 'a')},
        {'phrase_id': 'b', 'text': two, 'stanza_start': False,
         'chords': [dict(name='Bm', char_index=0, start_ms=2000, end_ms=4500, continued=True),
                    dict(name='E', char_index=8, start_ms=4500, end_ms=5500),
                    dict(name='F#7', char_index=two.index('верю'), start_ms=5500, end_ms=7000)],
         'display_words': words(two, [(3400, 3500), (3500, 3900), (3900, 4300), (4300, 4500), (4600, 5000),
                                      (5000, 5100), (5100, 5600)], 'b')}]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        tab = browser.new_page(viewport={'width': 900, 'height': 600})
        errors = []
        tab.on('pageerror', lambda e: errors.append(str(e)))
        tab.set_content('<!doctype html><html lang="ru"><meta charset="utf-8"><main></main></html>')
        tab.add_style_tag(path=str(ROOT / 'ui/studio/songs.css'))
        tab.evaluate('''window.escH=s=>{const e=document.createElement('div');e.textContent=String(s??'');return e.innerHTML.replaceAll('"','&quot;')};''')
        tab.add_script_tag(path=str(ROOT / 'ui/studio/song_sheet.js'))
        tab.evaluate('(row)=>{window.row=row;document.querySelector("main").innerHTML=SongSheet.sheetHTML(row)}', row)

        def state(ms):
            return tab.evaluate('''ms=>{
              SongSheet.updatePlayback(row.song_id,ms,false);
              const chord=document.querySelector('.current-chord');
              const lines=[...document.querySelectorAll('.song-paper-line')];
              return {chord:chord?.textContent, chordLine:lines.indexOf(chord?.closest('.song-paper-line')),
                      word:document.querySelector('.current-word')?.textContent,
                      count:document.querySelectorAll('.current-chord').length};
            }''', ms)
        at_boat, at_next, at_e = state(2300), state(3450), state(4800)
        assert at_boat == {'chord': 'Bm', 'chordLine': 0, 'word': 'лодке,', 'count': 1}, at_boat
        assert at_next == {'chord': 'Bm', 'chordLine': 1, 'word': 'я', 'count': 1}, at_next
        assert at_e['chord'] == 'E' and at_e['chordLine'] == 1, at_e
        assert not errors, errors
        browser.close()
    print('Held chord is lit on the line being sung: OK')


if __name__ == '__main__':
    main()
