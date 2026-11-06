"""Read-only real-song playback regression. No core, settings, audio or network."""
import argparse
import json
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--song', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--assets', type=Path, default=ROOT/'ui')
    args = parser.parse_args()
    row = json.loads(args.song.read_text())
    with tempfile.TemporaryDirectory(prefix='dewiar-timeline-') as temp, sync_playwright() as pw:
        page = Path(temp)/'test.html'
        page.write_text('''<!doctype html><html lang="ru"><meta charset="utf-8">
          <title>ТЕСТ — порядок строк — отдельные данные</title>
          <style>body{background:#dae6f1;padding:24px}#test-banner{background:#593b00;color:white;padding:12px}</style>
          <div id="test-banner">ТЕСТ — отдельные данные, рабочая карточка не изменяется</div><main></main></html>''')
        browser = pw.chromium.launch()
        tab = browser.new_page(viewport={'width':1280,'height':900})
        errors = []
        tab.on('pageerror', lambda e: errors.append(str(e)))
        tab.goto(page.as_uri())
        tab.add_style_tag(path=str(args.assets/'studio/songs.css'))
        tab.evaluate('''window.escH=s=>{const e=document.createElement('div');e.textContent=String(s??'');return e.innerHTML.replaceAll('"','&quot;')};''')
        tab.add_script_tag(path=str(args.assets/'studio/song_sheet.js'))
        tab.evaluate('(row)=>{window.row=row;document.querySelector("main").innerHTML=SongSheet.sheetHTML(row)}',row)
        lines = tab.locator('.song-paper-lyrics').all_text_contents()
        assert lines[0].startswith('Как много тех'), lines[:3]
        checks=[]
        for ms,kind in [(5000,'instrumental'),(16680,'word'),(19000,'word'),
                        (120000,'instrumental'),(127000,'instrumental'),
                        (128000,'instrumental'),(129000,'instrumental'),(130500,'word')]:
            state=tab.evaluate('''ms=>{
              SongSheet.updatePlayback(row.song_id,ms,true);
              const count=s=>document.querySelectorAll(s).length;
              return {ms,words:count('.current-word'),chords:count('.current-chord'),
                instruments:count('.playing-instrumental'),lines:count('.song-paper-line.playing-line'),
                lyricLines:count('.song-paper-lyrics.playing-line'),
                word:document.querySelector('.current-word')?.textContent,
                cue:document.querySelector('.countdown-lit')?.dataset.cueStep,
                line:document.querySelector('.song-paper-line.playing-line')?.textContent};
            }''',ms)
            assert state['lines']==1 and state['lyricLines']<=1 and state['chords']<=1,state
            assert state['words']==(1 if kind=='word' else 0),state
            assert state['instruments']==(1 if kind=='instrumental' else 0),state
            checks.append(state)
        assert checks[1]['word']=='Как',checks[1]
        assert [c.get('cue') for c in checks[4:7]]==['3','2','1'],checks[4:7]
        assert checks[-1]['word']=='так',checks[-1]
        # Deliberately overlapping cues used to leave two active chords/lines.
        tab.evaluate('''()=>{
          const spans=[...document.querySelectorAll('.song-chord-tip[data-start-ms]')];
          for (const c of spans) {c.dataset.startMs=0;c.dataset.endMs=150000;}
          SongSheet.updatePlayback(row.song_id,120000,false);
        }''')
        assert tab.locator('.current-chord').count()==1
        assert tab.locator('.playing-instrumental').count()==1
        assert tab.locator('.current-word').count()==0
        assert tab.locator('.song-paper-line.playing-line').count()==1
        # Render clean state for evidence and check copied lyric order.
        tab.evaluate('document.querySelector("main").innerHTML=SongSheet.sheetHTML(row)')
        copied=tab.evaluate('SongSheet.plainText(row)')
        assert copied.index('Как много тех') < copied.index('с кем можно лечь')
        args.report.parent.mkdir(parents=True,exist_ok=True)
        for name,ms in [('opening',16680),('break',128000)]:
            tab.evaluate('ms=>SongSheet.updatePlayback(row.song_id,ms,true)',ms)
            tab.locator('.song-paper-line.playing-line').scroll_into_view_if_needed()
            tab.screenshot(path=str(args.report.with_name(name+'.png')))
        assert not errors,errors
        args.report.write_text(json.dumps({'checks':checks,'errors':errors},ensure_ascii=False,indent=2))
        browser.close()
    print('Real-song order, break, 3-2-1, active word/chord, scrolling and copy: OK')


if __name__=='__main__': main()
