"""Native Cocoa canvas + real Bridge; isolated workspace, no engine/audio/network models."""
import os,sys,tempfile,threading,socket,http.server,functools,json,io,traceback
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageDraw,ImageFont
TEMP=tempfile.TemporaryDirectory(prefix='dewiar-canvas-native-');tmp=Path(TEMP.name)
os.environ['DEWIAR_CONFIG_DIR']=str(tmp/'config');os.environ['DEWIAR_STATE_DIR']=str(tmp/'state')
ROOT=Path(os.environ.get('DEWIAR_CANVAS_ROOT',Path(__file__).resolve().parents[1]));sys.path[:0]=[str(ROOT/'core'),str(ROOT/'ui')]
from app import LocalHTTPServer
from settings import Settings
from workspace import Workspace
from chatlog import ChatLog
from bridge import Bridge
from engine_commands import CommandsMixin
from app import Api
from AppKit import NSPasteboard
from macsys import clipboard_image, copy_png_file
import canvases as C
import webview
settings=Settings(tmp/'settings.json');settings.update({'workspace':{'path':str(tmp/'workspace')}});ws=Workspace(settings)
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
bridge=Bridge('native-canvas',port=port)
engine=SimpleNamespace(s=settings,workspace=ws,chatlog=ChatLog(tmp/'chat.jsonl'),emit=lambda typ,**data:bridge.broadcast({'type':typ,**data}))
bridge.handler=lambda cmd,args:{} if cmd=='hello' else CommandsMixin.command(engine,cmd,args);bridge.start();assert bridge.ready.is_set()
b=io.BytesIO();fixture_image=Image.new('RGB',(6016,3384),'white');ImageDraw.Draw(fixture_image).text((100,100),'Save project',font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',60),fill='black');fixture_image.save(b,'PNG');result=C.from_screenshot(engine,None,b.getvalue(),'synthetic','ТЕСТ — отдельные данные');identifier=result['canvas_id']
class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.split('?')[0]=='/canvas.html':
            body=(ROOT/'ui/canvas.html').read_text().replace('<strong>Холст и схемы</strong>','<strong>ТЕСТ — отдельные данные</strong>')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body.encode())
        else:super().do_GET()
server=LocalHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'ui')));threading.Thread(target=server.serve_forever,daemon=True).start()
board=NSPasteboard.pasteboardWithUniqueName()
fixture=tmp/'clipboard.png';fixture.write_bytes(b.getvalue());copy_png_file(fixture,board)
class TestApi(Api):
    def clipboard_image(self):return clipboard_image(board)
    def _url(self,page,extra=''):return f'http://127.0.0.1:{server.server_port}/{page}?token=native-canvas&port={port}'+extra
api=TestApi('native-canvas');api.open_canvas(identifier)
keeper=webview.create_window('ТЕСТ — отдельные данные',html='<b>ТЕСТ — отдельные данные</b>',width=350,height=120)
failures=[]
def js(win,script):
    event=threading.Event();items=[];win.evaluate_js(script,callback=lambda value:(items.append(value),event.set()))
    assert event.wait(50),'Native JS timeout';assert not isinstance(items[0],dict) or not items[0].get('error'),items[0];return items[0]
def ready(win):
    win.events.loaded.wait(20)
    js(win,"new Promise((resolve,reject)=>{let n=0;const timer=setInterval(()=>{if(window.canvasEditor){clearInterval(timer);resolve(true)}else if(++n>250){clearInterval(timer);reject(Error(document.querySelector('#status').textContent))}},100)})")
def run():
    try:
        win=api._canvas_window;ready(win);api.open_canvas(identifier);assert api._canvas_window is win
        win.evaluate_js('''document.querySelector('#canvas-chat-input').value='Обведи текст «Save» красным';document.querySelector('#canvas-chat-form').requestSubmit()''')
        js(win,"new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(!document.querySelector('#canvas-chat-send').disabled&&canvasEditor.api.getSceneElements().some(e=>e.type==='rectangle')){clearInterval(t);resolve(true)}else if(++n>400){clearInterval(t);reject(Error(document.querySelector('#canvas-chat-log').textContent))}},100)})")
        assert win.evaluate_js("canvasEditor.api.getSceneElements().some(e=>e.type==='rectangle'&&e.x>80&&e.x<130&&e.y>80&&e.y<160)")
        result=js(win,"""(async()=>{await canvasEditor.insertMermaid('flowchart LR\\n A[Начало] --> B[Готово]');const result=await canvasEditor.output('canvas_save_as');return {path:result.path,count:canvasEditor.api.getSceneElements().length}})().catch(e=>({error:String(e)}))""")
        with Image.open(result['path']) as im:assert im.width>=6016 and im.height>=3384
        win.evaluate_js("document.querySelector('#number').click()")
        from PyObjCTools import AppHelper
        AppHelper.callAfter(win.native.performClose_,None);assert win.events.closed.wait(20),'close did not complete'
        api.open_canvas(identifier);win=api._canvas_window;ready(win)
        assert win.evaluate_js('canvasEditor.api.getSceneElements().some(e=>e.customData?.dewiarNumber===1)')
        win.evaluate_js('window.canvasRequestClose()');assert win.events.closed.wait(20)
        if fixture_path:=os.environ.get('DEWIAR_OCR_FIXTURE'):
            fixture_bytes=Path(fixture_path).read_bytes()
            russian=C.from_screenshot(engine,None,fixture_bytes,'fixture','ТЕСТ — отдельные данные')['canvas_id']
            api.open_canvas(russian);win=api._canvas_window;ready(win)
            def submit(text):
                win.evaluate_js("document.querySelector('#canvas-chat-input').value="+json.dumps(text)+";document.querySelector('#canvas-chat-form').requestSubmit()")
                js(win,"new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(!document.querySelector('#canvas-chat-send').disabled&&document.querySelector('#canvas-chat-input').value===''){clearInterval(t);resolve(true)}else if(++n>400){clearInterval(t);reject(Error(document.querySelector('#canvas-chat-log').textContent))}},100)})")
            def apply_proposal():
                win.evaluate_js("document.querySelector('#canvas-chat-apply').click()")
                js(win,"new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(document.querySelector('#canvas-chat-proposal').hidden&&!document.querySelector('#canvas-chat-send').disabled){clearInterval(t);resolve(true)}else if(++n>200){clearInterval(t);reject(Error(document.querySelector('#canvas-chat-log').textContent))}},100)})")
            submit('Подчеркни «Дело было ночью»')
            assert win.evaluate_js("document.querySelectorAll('#canvas-chat-candidates input').length===3")
            assert win.evaluate_js("document.activeElement.id==='canvas-chat-apply'")
            win.evaluate_js("document.querySelector('#canvas-chat-candidates input[value=all]').checked=true")
            apply_proposal();assert win.evaluate_js("canvasEditor.api.getSceneElements().filter(e=>e.type==='line').length===2")
            submit('Удали «Я спросил"');apply_proposal()
            assert win.evaluate_js("canvasEditor.api.getSceneElements().some(e=>e.customData?.dewiarTextCover)")
            exported=js(win,"canvasEditor.output('canvas_save_as')")
            with Image.open(exported['path']) as im:
                pad=(im.width-Image.open(fixture_path).width)//2
                assert im.convert('RGB').crop((150+pad,780+pad,380+pad,820+pad)).getextrema()==((255,255),)*3
                assert im.convert('RGB').crop((400+pad,780+pad,600+pad,815+pad)).getextrema()[0][0]<80
            submit('Отмени');assert not win.evaluate_js("canvasEditor.api.getSceneElements().some(e=>e.customData?.dewiarTextCover)")
            assert Path(fixture_path).read_bytes()==fixture_bytes
            win.evaluate_js('window.canvasRequestClose()');assert win.events.closed.wait(20)
            print('PASS: native real Russian fixture; two matches/all; mixed quote deletion; PNG pixels; neighbor preserved; Undo; original unchanged',flush=True)
        api.open_canvas();win=api._canvas_window;win.events.loaded.wait(20)
        js(win,"new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(document.querySelector('#status').textContent.includes('автоматически')){clearInterval(t);resolve(true)}else if(++n>200){clearInterval(t);reject(Error('empty page timeout'))}},100)})")
        win.evaluate_js("document.querySelector('#paste-image').click()")
        ready(win)
        js(win,"new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(canvasEditor.api.getSceneElements().filter(e=>e.type==='image').length===1&&document.querySelector('#status').textContent==='Изображения добавлены без уменьшения'){clearInterval(t);resolve(true)}else if(++n>200){clearInterval(t);reject(Error(document.querySelector('#status').textContent))}},100)})")
        assert win.evaluate_js("canvasEditor.api.getSceneElements()[0].width===6016")
        # Native bridge reads a private real macOS pasteboard; user's clipboard is untouched.
        win.evaluate_js("document.querySelector('#canvas-root').dispatchEvent(new KeyboardEvent('keydown',{key:'v',code:'KeyV',ctrlKey:true,bubbles:true,cancelable:true}))")
        js(win,"new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(canvasEditor.api.getSceneElements().filter(e=>e.type==='image').length===2&&document.querySelector('#status').textContent==='Изображения добавлены без уменьшения'){clearInterval(t);resolve(true)}else if(++n>200){clearInterval(t);reject(Error('second paste timeout'))}},100)})")
        print('PASS: native WKWebView; 6K PNG; editable Cyrillic Mermaid; single window; local chat OCR Save; system close saves last edit; reopen; empty canvas native clipboard button/Ctrl+V preserves 6K',flush=True)
    except Exception:traceback.print_exc();failures.append(True)
    finally:
        win=api._canvas_window
        if win:win.evaluate_js('window.canvasRequestClose()');win.events.closed.wait(15)
        keeper.destroy();board.releaseGlobally()
webview.start(run);server.shutdown();server.server_close();bridge.pool.shutdown(wait=True);TEMP.cleanup()
assert not failures
