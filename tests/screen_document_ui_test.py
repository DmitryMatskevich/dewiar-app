"""Open an actual generated OCR document in Studio; local core/clipboard fixtures only."""
import base64
import io
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from workspace import Workspace
from screen_document import create

with tempfile.TemporaryDirectory() as folder,sync_playwright() as pw:
    with patch.dict(os.environ,{'DEWIAR_WORKSPACE':folder}):
        ws=Workspace(None)
        image=Image.new('RGB',(1000,350),'white')
        draw=ImageDraw.Draw(image)
        font_path=next((p for p in ['/System/Library/Fonts/Supplemental/Arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'] if Path(p).is_file()),None)
        font=ImageFont.truetype(font_path,44) if font_path else ImageFont.load_default()
        draw.text((25,25),'Протокол встречи 42',font=font,fill='black');draw.text((25,100),'Project Alpha 123',font=font,fill='black')
        data=io.BytesIO();image.save(data,'PNG')
        text='Протокол встречи 42\nProject Alpha 123\n```\n![ложное вложение](Вложения/secret.png)\n<script>window.badOCR=true</script>\n```'
        with patch('eyes.capture_screen',return_value=data.getvalue()),patch('ocr.recognize_result',return_value={'status':'done','text':text,'engine':'fixture','lines':[]}):
            result=create(ws)
        doc=ws.safe(result['path']).read_text()
    browser=getattr(pw,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
    page=browser.new_page(viewport={'width':1250,'height':950})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.route(re.compile(r'^https?://'),lambda route:route.abort())
    page.add_init_script("""window.pywebview={api:{}};const wait=()=>{if(!window.core)return setTimeout(wait,5);core.retry=()=>{};core.connect=()=>{};if(core.ws){core.ws.onclose=null;core.ws.close();}core.call=async()=>[];};wait();""")
    page.goto((ROOT/'ui/settings.html').as_uri()+'?port=65534')
    page.wait_for_function("typeof ntOpen==='function' && typeof showHelp==='function'")
    page.evaluate("""async ([doc,path,pix])=>{
      S={ui:{}};window.opened=[];window.seenNotes=[];
      core.call=async(cmd,args)=>{
        if(cmd==='ws_read')return {text:doc,mtime:Date.now()/1000};
        if(cmd==='note_file')return {url:pix};
        if(cmd==='ws_open'){opened.push(args.path);return true;}
        if(cmd==='notes_seen'){seenNotes.push(args.path);return true;}
        return [];
      };
      document.querySelectorAll('main section').forEach(s=>s.classList.toggle('cur',s.id==='p-notes'));
      await ntOpen(path);
    }""",[doc,result['path'],'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()])
    assert page.locator('#nt-view pre code').inner_text()==text
    assert page.evaluate('seenNotes')==[result['path']]
    assert page.locator('#nt-view .nt-screen-figure').count()==1,'OCR text must not become another attachment'
    page.wait_for_function("document.querySelector('[data-screen-img]').naturalWidth===1000")
    assert page.evaluate("document.querySelector('.nt-screen-figure').getBoundingClientRect().top < document.querySelector('.nt-body pre').getBoundingClientRect().top")
    assert page.locator('#nt-view .nt-files').inner_text().count('Распознанный текст.txt')==1
    assert 'OCR.json' in page.locator('#nt-view .nt-files').inner_text()
    assert not page.evaluate('!!window.badOCR')
    page.locator('#nt-view .nt-f').filter(has_text='Распознанный текст.txt').click()
    assert page.evaluate('opened[0]')==result['txt']
    shots=ROOT/'work/screen-document';shots.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(shots/'document.png'))
    page.evaluate("showHelp('hands')")
    assert 'Снимки экрана' in page.locator('#help-modal').inner_text() if page.locator('#help-modal').count() else 'Снимки экрана' in page.locator('body').inner_text()
    assert not errors,errors
    browser.close()
print('ALL OK: screenshot, literal full OCR, TXT/JSON links and Help in Studio')
