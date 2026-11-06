"""Read-only regression on Aramis recording, using the real sheet and player."""
import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from songs.public import public_song


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--song',type=Path,required=True)
    parser.add_argument('--timing',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--assets',type=Path,default=ROOT/'ui')
    args=parser.parse_args()
    original=args.song.read_bytes()
    row=json.loads(original)
    row['phrases']=json.loads(args.timing.read_text())['phrases']
    row['capabilities']['audio']=True
    audio=args.song.parent.parent/row['audio']['path']
    media='data:audio/webm;base64,'+base64.b64encode(audio.read_bytes()).decode()
    row=public_song(row)
    outcomes=[]
    with tempfile.TemporaryDirectory(prefix='dewiar-vocal-gap-') as tmp, sync_playwright() as pw:
        html=Path(tmp)/'index.html'
        html.write_text('''<!doctype html><meta charset="utf-8"><title>ТЕСТ — отдельные данные</title>
          <style>body{background:#dae6f1;padding:24px}aside{background:#593b00;color:white;padding:12px}</style>
          <aside>ТЕСТ — отдельные данные; настоящая запись, карточка не изменяется</aside><main></main>''')
        browser=pw.chromium.launch()
        tab=browser.new_page(viewport={'width':1280,'height':900})
        errors=[]
        tab.on('pageerror',lambda e:errors.append(str(e)))
        tab.goto(html.as_uri())
        tab.add_style_tag(path=str(args.assets/'studio/songs.css'))
        tab.evaluate('''window.escH=s=>{const e=document.createElement('div');e.textContent=String(s??'');return e.innerHTML.replaceAll('"','&quot;')};''')
        for name in ('song_sheet.js','song_review.js'):
            tab.add_script_tag(path=str(args.assets/'studio'/name))
        tab.evaluate('''({row,media})=>{
          window.row=row;window.core={call:async(cmd)=>{
            if(cmd==='song_full_audio')return {data:media};throw Error('Unexpected mutation: '+cmd);
          }};
          document.querySelector('main').innerHTML=SongReview.playerHtml(row)+SongSheet.sheetHTML(row);
          SongReview.bind(row,()=>row,()=>{throw Error('No card writes allowed')});
          document.querySelector('audio').muted=true;
        }''',dict(row=row,media=media))
        tab.evaluate('SongReview.toggleFull(false)')
        tab.wait_for_function('document.querySelector("audio").readyState>=2')
        tab.evaluate('document.querySelector("audio").pause()')
        for ms,word,instrumental in [(5000,None,True),(60000,None,True),(74000,None,True),
                                     (75000,None,True),(76000,None,True),(77000,None,True),
                                     (77700,'Где-то',False),(80900,'ветра',False),
                                     (70000,None,True),(77700,'Где-то',False)]:
            tab.evaluate('''ms=>new Promise(resolve=>{
              const a=document.querySelector('audio');a.addEventListener('seeked',()=>resolve(),{once:true});a.currentTime=ms/1000;
            })''',ms)
            state=tab.evaluate('''()=>({
              word:document.querySelector('.song-paper .current-word')?.textContent||null,
              instrumental:!!document.querySelector('.playing-instrumental'),
              chord:document.querySelector('.current-chord')?.textContent,
              cue:document.querySelector('.countdown-lit')?.dataset.cueStep,
              position:document.querySelector('audio').currentTime,
              rate:document.querySelector('audio').playbackRate
            })''')
            assert state['word']==word and state['instrumental']==instrumental,(ms,state)
            assert abs(state['position']-ms/1000)<.01 and state['rate']==1,state
            outcomes.append(dict(ms=ms,**state))
        assert [c['cue'] for c in outcomes[3:6]]==['3','2','1']
        # Real media clock, not a manually supplied UI timestamp.
        tab.evaluate('document.querySelector("audio").play()')
        tab.wait_for_function('document.querySelector("audio").currentTime>77.9')
        tab.evaluate('document.querySelector("audio").pause()')
        assert tab.locator('.song-paper .current-word').inner_text()=='Где-то'
        args.report.parent.mkdir(parents=True,exist_ok=True)
        tab.locator('.song-paper .current-word').scroll_into_view_if_needed()
        tab.screenshot(path=str(args.report.with_suffix('.png')))
        assert not errors,errors
        assert args.song.read_bytes()==original
        args.report.write_text(json.dumps(dict(checks=outcomes,errors=errors),ensure_ascii=False,indent=2))
        browser.close()
    print('Real recording: break, 3-2-1, word timing, seek/replay and pause: OK')


if __name__=='__main__':main()
