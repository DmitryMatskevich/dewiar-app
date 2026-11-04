"""Read-only browser audit of final chord rows, including gaps between phrases.

Optional --library reads a public-song JSON snapshot; no live core or models.
The real UI is loaded from a temporary copy and every API call is intercepted.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from songs_ui_preview import page as preview_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--assets', type=Path, default=ROOT / 'ui')
    parser.add_argument('--theme', choices=('light', 'dark'), default='light')
    args = parser.parse_args()
    rows = json.loads(args.library.read_text()) if args.library else [{
        'song_id': 'boundary', 'title': 'ТЕСТ — пауза между фразами', 'revision': 1,
        'phrases': [], 'capabilities': {}, 'guitar_sheet': [
            {'text': '', 'unplaced_chords': [{'name': 'F6', 'start_ms': 0, 'end_ms': 100}]},
            {'text': '', 'unplaced_chords': [{'name': 'Fdim', 'start_ms': 100, 'end_ms': 300}]},
            {'text': 'Начало фразы остаётся целиком.', 'chords': [
                {'name': 'F', 'char_index': 0, 'start_ms': 300, 'end_ms': 3000}]}]}]
    outcomes = []
    with tempfile.TemporaryDirectory(prefix='dewiar-sheet-audit-') as tmp, sync_playwright() as pw:
        ui = Path(tmp) / 'ui'
        shutil.copytree(args.assets, ui)
        html = preview_page('fixture').decode().replace('="/studio/', '="studio/')
        html = html.replace('<link rel="stylesheet" href="studio/songs.css">',
                            '<link rel="stylesheet" href="assets/fonts.css"><link rel="stylesheet" href="assets/skins.css">'
                            '<link rel="stylesheet" href="studio/theme.css"><link rel="stylesheet" href="studio/songs.css">')
        if args.theme == 'light':
            html = html.replace('<html lang="ru">', '<html lang="ru" class="st-skin st-light st-neu">')
        start, end = html.index('const core='), html.index('</script>', html.index('const core='))
        mock = '''window.rows=ROWS;const core={on(){},async call(cmd,a){
          if(cmd==='songs_list')return structuredClone(rows);
          if(cmd==='song_get')return structuredClone(rows.find(r=>r.song_id===a.song_id));
          throw Error('Unexpected command in read-only layout test: '+cmd);
        }};'''.replace('ROWS', json.dumps(rows).replace('</', '<\\/'))
        (ui / 'test.html').write_text(html[:start] + mock + html[end:])
        browser = pw.chromium.launch()
        tab = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        tab.on('pageerror', lambda e: errors.append(str(e)))
        tab.goto((ui / 'test.html').as_uri())
        for row in rows:
            tab.locator(f'.song-card-actions [data-song-open="{row["song_id"]}"]').click()
            tab.wait_for_function('(id)=>document.querySelector(".song-paper")?.dataset.songId===id', arg=row['song_id'])
            before = tab.evaluate('JSON.stringify(rows)')
            for width in (375, 800, 1440):
                tab.set_viewport_size({'width': width, 'height': 900})
                # Let ResizeObserver and its replacement sheet settle.
                tab.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                for delta in (0, 1, -1):
                    if delta:
                        tab.locator('#song-key-up' if delta > 0 else '#song-key-down').click()
                    result = tab.evaluate('''(row)=>{
                      const es=[...document.querySelectorAll('.song-paper-body .song-paper-chords, .song-paper-body .song-paper-lyrics, .song-paper-body .song-paper-instrumental, .song-paper-body > .song-paper-space')];
                      const visible=es.filter(e=>!e.classList.contains('song-paper-space'));
                      const chords=es.filter(e=>e.classList.contains('song-paper-chords'));
                      const lyrics=es.filter(e=>e.classList.contains('song-paper-lyrics'));
                      const words=s=>s.trim().split(/\\s+/).filter(Boolean).join(' ');
                      return {
                        no_stacks:visible.every((e,i)=>!e.classList.contains('song-paper-chords') || !visible[i-1]?.classList.contains('song-paper-chords')),
                        attached_to_lyrics:chords.every((e)=>{
                          const next=visible[visible.indexOf(e)+1];
                          return next && (next.classList.contains('song-paper-lyrics') || next.classList.contains('song-paper-instrumental')) && e.parentElement.classList.contains('song-paper-line') && e.parentElement===next.parentElement && Math.abs(next.getBoundingClientRect().top-e.getBoundingClientRect().bottom)<1;
                        }),
                        instrumental_labels:es.filter(e=>e.classList.contains('song-paper-instrumental')).length===row.guitar_sheet.filter(l=>!l.text && [...(l.chords||[]),...(l.unplaced_chords||[])].some(c=>c.name && c.name!=='N')).length,
                        one_visual_row:chords.every(e=>e.getBoundingClientRect().height<=parseFloat(getComputedStyle(e).lineHeight)+1),
                        fits:chords.every(e=>{const r=document.createRange();r.selectNodeContents(e);return r.getBoundingClientRect().right<=e.getBoundingClientRect().right+1}),
                        overflow:chords.flatMap(e=>{const r=document.createRange();r.selectNodeContents(e);const a=r.getBoundingClientRect(),b=e.getBoundingClientRect();return a.right>b.right+1?[{text:e.textContent,width:a.width,available:b.width}]:[]}),
                        all_words:words(lyrics.map(e=>e.textContent).join(' '))===words(row.guitar_sheet.map(l=>l.text).join(' ')),
                        copy_matches:SongSheet.plainText(row)===es.map(e=>e.classList.contains('song-paper-space')?'':e.textContent).join('\\n').replace(/^\\n+|\\n+$/g,''),
                        chord_rows:chords.length
                      };
                    }''', row)
                    assert all(result[k] for k in ('no_stacks', 'attached_to_lyrics', 'instrumental_labels', 'one_visual_row', 'fits', 'all_words', 'copy_matches')), (row['title'], width, delta, result)
                    outcomes.append({'title': row['title'], 'width': width, 'shift': delta > 0, **result})
            assert tab.evaluate('JSON.stringify(rows)') == before
            if args.report and 'тоже есть душа' in row['title']:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                tab.locator('.song-paper').screenshot(path=str(args.report.with_suffix('.png')))
            if args.report and 'Яцевич' in row.get('artist', ''):
                args.report.parent.mkdir(parents=True, exist_ok=True)
                tab.locator('.song-paper-line').filter(has_text='Пап, ты без меня сможешь?').first.screenshot(path=str(args.report.with_name('papa-chord-lyric.png')))
            print('OK:', row['title'], '— 3 widths, 3 key states, all words, copy and chord rows')
        assert not errors, errors
        browser.close()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'checks': outcomes, 'page_errors': errors}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
