"""Real Cocoa WKWebView + production WebSocket bridge, isolated data, no engine/models.
Set DEWIAR_VISUAL_ROOT to a signed bundle's Contents/Resources to test its files.
Run external Python with -B for bundle checks.
"""
import functools,http.server,threading,json,sys,tempfile,os,socket,time,traceback
from pathlib import Path
from types import SimpleNamespace
TEMP=tempfile.TemporaryDirectory(prefix='dewiar-visual-native-');tmp=Path(TEMP.name)
os.environ['DEWIAR_CONFIG_DIR']=str(tmp/'config');os.environ['DEWIAR_STATE_DIR']=str(tmp/'state')
ROOT=Path(os.environ.get('DEWIAR_VISUAL_ROOT',Path(__file__).resolve().parents[1]))
sys.path[:0]=[str(ROOT/'core'),str(ROOT/'ui')]
from settings import Settings
from workspace import Workspace
from chatlog import ChatLog
from engine_commands import CommandsMixin
from bridge import Bridge
from app import Api
from PIL import Image,ImageDraw,ImageFont
import webview
import faulthandler
faulthandler.dump_traceback_later(45,repeat=False)
settings=Settings(tmp/'config/settings.json');settings.update({'workspace':{'path':str(tmp/'workspace')}})
ws=Workspace(settings);chat=ChatLog(tmp/'chat.jsonl')
src=ws.safe('Входящие/Снимки экрана/ТЕСТ.png');src.parent.mkdir(parents=True)
im=Image.new('RGB',(6016,3384),'white');ImageDraw.Draw(im).text((400,300),'Save project',fill='black',font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',100));im.save(src)
item=chat.add('assistant','ТЕСТ — отдельные данные',att=[{'kind':'image','name':'ТЕСТ','path':str(src.relative_to(ws.root)),'capture':{'text':'PRIVATE ORIGINAL','status':'done'}}]);identifier=item['id']
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
bridge=Bridge('test-only',port=port)
engine=SimpleNamespace(s=settings,workspace=ws,chatlog=chat,emit=lambda typ,**data:bridge.broadcast({'type':typ,**data}))
bridge.handler=lambda cmd,args: {} if cmd=='hello' else CommandsMixin.command(engine,cmd,args)
bridge.start();assert bridge.ready.is_set()
class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.split('?')[0]=='/visual-editor.html':
            body=(ROOT/'ui/visual-editor.html').read_text().replace('<strong>Пометки на снимке</strong>','<strong>ТЕСТ — отдельные данные</strong>').replace('<title>Dewiar','<title>ТЕСТ')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body.encode())
        else:super().do_GET()
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'ui')))
threading.Thread(target=server.serve_forever,daemon=True).start()
class TestApi(Api):
    def _url(self,page,extra=''):return f'http://127.0.0.1:{server.server_port}/{page}?token=test-only&port={port}'+extra
api=TestApi('test-only');api.open_visual_editor('screenshot',identifier)
keeper=webview.create_window('ТЕСТ — отдельные данные — проверка',html='<b>ТЕСТ — отдельные данные</b>',width=350,height=120)
result=[]
def js(win,script):
    done=threading.Event();value=[]
    win.evaluate_js(script,callback=lambda r:(value.append(r),done.set()))
    assert done.wait(40),'WebKit JS timeout'
    assert not isinstance(value[0],dict) or 'error' not in value[0],value[0]
    return value[0]
def ready(win):
    win.events.loaded.wait(20)
    return js(win,"new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(window.visualEditor){clearInterval(t);resolve(true)}else if(++n>200){clearInterval(t);reject(Error(document.querySelector('#status').textContent))}},100)})")
def run():
    try:
        win=api._visual_windows[identifier];ready(win)
        api.open_visual_editor('screenshot',identifier);assert api._visual_windows[identifier] is win
        result.append(js(win,"""(async()=>{const e=visualEditor;document.querySelector('[data-tool=number]').click();await e.flush();const r=await e.output('visual_save_as');return {path:r.path,width:e.doc.source.width,ua:navigator.userAgent,markers:e.area.getState().markers.length}})().catch(e=>({error:String(e)}))"""))
        with Image.open(result[-1]['path']) as out:assert out.size==(6016,3384)
        # Close immediately after another edit: production close hook must flush it.
        win.evaluate_js("document.querySelector('[data-tool=number]').click()")
        from PyObjCTools import AppHelper
        AppHelper.callAfter(win.native.performClose_,None)
        assert win.events.closed.wait(20),'close did not save/finish'
        api.open_visual_editor('screenshot',identifier);win=api._visual_windows[identifier];ready(win)
        assert win.evaluate_js('visualEditor.area.getState().markers.length')==2
        result.append({'reopen_markers':2,'close_saved':True,'same_window':True})
        result.append(js(win,'''(async()=>{await visualEditor.executeCommand('подчеркни «Save» красным');const markers=visualEditor.area.getState().markers;if(markers.length!==3||markers[2].typeName!=='LineMarker')throw Error(document.querySelector('#status').textContent);await visualEditor.output('visual_save_as');return {ocr_command:true,marker:markers[2].typeName}})().catch(e=>({error:String(e)}))'''))
        print('PASS native WKWebView + real Bridge: '+json.dumps(result,ensure_ascii=False),flush=True)
    except Exception:
        traceback.print_exc();result.append({'failed':True})
    finally:
        for win in list(api._visual_windows.values()):
            win.evaluate_js('window.visualRequestClose()');win.events.closed.wait(15)
        keeper.destroy()
webview.start(run)
server.shutdown();server.server_close();bridge.pool.shutdown(wait=True);TEMP.cleanup()
faulthandler.cancel_dump_traceback_later()
assert not any(r.get('failed') for r in result),result
