"""Local canvas acceptance with production Bridge and real Chromium/WebKit. No models/mic."""
import os,sys,tempfile,json,threading,socket,functools,http.server,hashlib,base64,io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from playwright.sync_api import sync_playwright
TEMP=tempfile.TemporaryDirectory(prefix='dewiar-canvas-');tmp=Path(TEMP.name)
os.environ['DEWIAR_CONFIG_DIR']=str(tmp/'config');os.environ['DEWIAR_STATE_DIR']=str(tmp/'state')
ROOT=Path(os.environ.get('DEWIAR_CANVAS_ROOT',Path(__file__).resolve().parents[1]));sys.path[:0]=[str(ROOT/'core'),str(ROOT/'ui')]
from app import LocalHTTPServer
from settings import Settings
from workspace import Workspace
from chatlog import ChatLog
from bridge import Bridge
from engine_commands import CommandsMixin
import canvases as C
settings=Settings(tmp/'config/settings.json');settings.update({'workspace':{'path':str(tmp/'workspace')}})
settings.update({'llm':{'profiles':{'canvas-test-model':{'kind':'openai','name':'ТЕСТ модель','base_url':'https://example.invalid/v1','model':'fixture','roles':['images']}}}})
ws=Workspace(settings);chat=ChatLog(tmp/'chat.jsonl');task=SimpleNamespace(id='canvas-task',dir=tmp/'task');task.dir.mkdir()
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
bridge=Bridge('canvas-test',port=port)
engine=SimpleNamespace(workspace=ws,chatlog=chat,s=settings,tasks=SimpleNamespace(list=lambda:[{'id':task.id,'title':'ТЕСТ задача'}],get=lambda tid:task,create=Mock(return_value=task),tell=Mock()),emit=lambda typ,**data:bridge.broadcast({'type':typ,**data}))
frames=[]
def command(cmd,args):
    frames.append(len(json.dumps(args).encode()))
    if cmd=='hello':return {}
    if cmd=='canvas_copy':
        from AppKit import NSPasteboard
        from macsys import copy_png_file
        folder,doc=C.read(engine,args['canvas_id']);path=C.exported(folder,doc);board=NSPasteboard.pasteboardWithUniqueName()
        copy_png_file(path,board);assert bytes(board.dataForType_('public.png'))==path.read_bytes();board.releaseGlobally();return True
    if cmd=='canvas_chat_plan' and args.get('auto_model') and args.get('text')=='Предложи оформление':
        # Explicit-model UI is verified without network or a billable request.
        reply=json.dumps({'reply':'Предлагаю зелёный фон','operations':[{'op':'background','color':'#00ff00'}]})
        with patch('llm.stream_chat',return_value=iter([reply])):return CommandsMixin.command(engine,cmd,args)
    return CommandsMixin.command(engine,cmd,args)
bridge.handler=command;bridge.start();assert bridge.ready.is_set()
doc=command('canvas_create',{'title':'ТЕСТ — отдельные данные'});identifier=doc['id']
# Revisions, file types, upload boundaries, digest and cancellation are server enforced.
try:C.command(engine,'canvas_open',{'canvas_id':'../escape'});raise AssertionError('traversal')
except ValueError:pass
args={'canvas_id':identifier,'purpose':'image','size':3,'sha256':hashlib.sha256(b'abc').hexdigest()}
up=C.command(engine,'canvas_upload_begin',args)
try:C.command(engine,'canvas_upload_chunk',{'canvas_id':identifier,'upload_id':up['upload_id'],'offset':1,'data':'YWJj'});raise AssertionError('offset')
except ValueError:pass
C.command(engine,'canvas_upload_cancel',{'canvas_id':identifier,'upload_id':up['upload_id']})
try:C.command(engine,'canvas_save',{'canvas_id':identifier,'base_revision':1,'scene':{}});raise AssertionError('revision')
except ValueError:pass
# Incompressible full-size source above the real 16 MiB bridge limit.
large=tmp/'large.png';Image.frombytes('RGB',(3000,2000),os.urandom(18_000_000)).save(large)
assert large.stat().st_size>16*1024*1024
small=tmp/'small.png';Image.new('RGB',(80,60),'red').save(small)
class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.split('?')[0]=='/canvas.html':
            body=(ROOT/'ui/canvas.html').read_text().replace('<strong>Холст и схемы</strong>','<strong>ТЕСТ — отдельные данные</strong>')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body.encode())
        else:super().do_GET()
server=LocalHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(ROOT/'ui')));threading.Thread(target=server.serve_forever,daemon=True).start()
artifacts=Path(__file__).resolve().parents[1]/'work/visual-p2';artifacts.mkdir(parents=True,exist_ok=True)
with sync_playwright() as playwright:
    kind=os.environ.get('DEWIAR_TEST_BROWSER','chromium');browser=getattr(playwright,kind).launch();page=browser.new_page(viewport={'width':1280,'height':900});errors=[];external=[]
    page.on('console',lambda m:print('Browser error:',m.text,flush=True) if m.type=='error' else None)
    page.on('requestfailed',lambda r:print('Request failed:',r.url,r.failure,flush=True))
    page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:external.append(r.url) if r.url.startswith(('http://','https://')) and not r.url.startswith('http://127.0.0.1:') else None)
    # Empty landing page: every entry path must create a document and keep its image.
    small_b64=base64.b64encode(small.read_bytes()).decode()
    base_url=f'http://127.0.0.1:{server.server_port}/canvas.html?token=canvas-test&port={port}'
    for entry in ('file','paste','drop','button','ctrl-v'):
        page.goto(base_url)
        page.wait_for_function("document.querySelector('#status').textContent.includes('автоматически')")
        before=len(C.command(engine,'canvas_list',{}))
        assert page.locator('#images').is_enabled()
        if entry=='file':
            with page.expect_file_chooser() as chooser:page.locator('#images').click()
            chooser.value.set_files(str(small))
        elif entry in ('paste','drop'):
            page.evaluate("""({entry,data})=>{const bytes=Uint8Array.from(atob(data),c=>c.charCodeAt(0));const files=new DataTransfer();files.items.add(new File([bytes],'fixture.png',{type:'image/png'}));const event=entry==='paste'?new ClipboardEvent('paste',{clipboardData:files,bubbles:true,cancelable:true}):new DragEvent('drop',{dataTransfer:files,bubbles:true,cancelable:true});document.querySelector('#canvas-root').dispatchEvent(event)}""",{'entry':entry,'data':small_b64})
        else:
            page.evaluate("data=>{window.clipboardReads=0;window.pywebview={api:{clipboard_image:async()=>{window.clipboardReads++;return 'data:image/png;base64,'+data}}}}",small_b64)
            if entry=='button':page.locator('#paste-image').click()
            else:page.keyboard.press('Control+v')
        page.wait_for_function("window.canvasEditor?.api.getSceneElements().filter(e=>e.type==='image').length===1")
        page.wait_for_function("document.querySelector('#status').textContent==='Изображения добавлены без уменьшения'")
        created=page.evaluate('canvasEditor.doc.id')
        assert len(C.command(engine,'canvas_list',{}))==before+1
        assert len(C.read(engine,created)[1]['elements'])==1
        assert page.evaluate("new URLSearchParams(location.search).get('id')") == created
        if entry=='ctrl-v':
            page.locator('#title').focus();page.keyboard.press('Control+v')
            assert page.evaluate('clipboardReads')==1  # Do not steal text-field shortcuts.
        page.reload();page.wait_for_function('window.canvasEditor')
        assert page.evaluate("canvasEditor.api.getSceneElements().filter(e=>e.type==='image').length")==1
    print('PASS: empty canvas file chooser, paste event, drop, clipboard button, Ctrl+V; one document; persisted; text input preserved',flush=True)
    page.goto(base_url)
    page.wait_for_function("!document.querySelector('#canvas-chat-send').disabled")
    def chat_send(text):
        page.locator('#canvas-chat-input').fill(text);page.locator('#canvas-chat-send').click()
        page.wait_for_function("!document.querySelector('#canvas-chat-send').disabled&&document.querySelector('#canvas-chat-input').value===''")
    chat_send('Добавь текст «Итого» в точке 120, 240')
    page.wait_for_function("window.canvasEditor?.api.getSceneElements().length===1")
    chat_id=page.evaluate('canvasEditor.doc.id')
    chat_send('Обведи текст «Итого» красным')
    page.wait_for_function('canvasEditor.api.getSceneElements().length===2')
    assert page.evaluate("canvasEditor.api.getSceneElements().some(e=>e.type==='rectangle'&&e.strokeColor==='#e5484d')")
    # Chat edits enter Excalidraw's undo stack as a normal, single action.
    page.locator('.excalidraw').focus();page.keyboard.press('Meta+z')
    try:page.wait_for_function('canvasEditor.api.getSceneElements().length===1',timeout=5000)
    except Exception:
        print(page.evaluate('({elements:canvasEditor.api.getSceneElements().map(e=>e.type),active:document.activeElement?.className,selection:canvasEditor.api.getAppState().selectedElementIds})'),flush=True);raise
    chat_send('Верни')
    page.wait_for_function('canvasEditor.api.getSceneElements().length===2')
    chat_send('Отмени')
    page.wait_for_function('canvasEditor.api.getSceneElements().length===1')
    chat_send('Перемести #1 в 400, 180')
    assert page.evaluate("canvasEditor.api.getSceneElements()[0].x") == 400
    chat_send('Измени цвет #1 на синий')
    assert page.evaluate("canvasEditor.api.getSceneElements()[0].strokeColor") == '#1971c2'
    chat_send('Сделай фон белым')
    chat_send('Предложи оформление')
    page.wait_for_function("!document.querySelector('#canvas-chat-proposal').hidden")
    assert page.evaluate('canvasEditor.api.getAppState().viewBackgroundColor')=='#ffffff'
    page.locator('#canvas-chat-apply').click()
    page.wait_for_function("canvasEditor.api.getAppState().viewBackgroundColor==='#00ff00'")
    page.wait_for_function("document.querySelector('#canvas-chat-proposal').hidden")
    chat_send('Удали #1')
    assert page.evaluate('canvasEditor.api.getSceneElements().length')==0
    page.reload();page.wait_for_function('window.canvasEditor')
    page.wait_for_function("document.querySelector('#canvas-chat-log').textContent.includes('Удали #1')")
    assert page.locator('#canvas-chat-model').count()==0
    assert page.locator('#canvas-model-settings').is_hidden()
    page.set_viewport_size({'width':640,'height':800})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(artifacts/('chat-'+kind+'.png')))
    page.set_viewport_size({'width':1280,'height':900})
    print('PASS: canvas chat empty start; text/outline; undo; move/color/delete; explicit model preview/apply; history; narrow layout',flush=True)
    if fixture_path:=os.environ.get('DEWIAR_OCR_FIXTURE'):
        original=Path(fixture_path).read_bytes()
        page.goto(base_url);page.wait_for_function("!document.querySelector('#canvas-chat-send').disabled")
        page.locator('#image-files').set_input_files(fixture_path)
        page.wait_for_function("document.querySelector('#status').textContent==='Изображения добавлены без уменьшения'")
        chat_send('Обведи «Марк»')
        page.evaluate("canvasEditor.api.updateScene({appState:{selectedElementIds:{[canvasEditor.api.getSceneElements().find(e=>e.type==='rectangle').id]:true}}})")
        chat_send('Подчеркни «Дело было ночью»')
        assert page.locator('#canvas-chat-proposal').is_visible()
        assert page.locator('#canvas-chat-candidates input').count()==3
        assert page.locator('#canvas-chat-candidates').inner_text().count('Дело было ночью')==2
        assert page.evaluate("document.activeElement.id==='canvas-chat-apply'")
        b=page.locator('#canvas-chat-apply').bounding_box();assert 0<=b['y']<900
        page.locator('#canvas-chat-candidates input[value="1"]').check();page.locator('#canvas-chat-apply').click()
        page.wait_for_function("document.querySelector('#canvas-chat-proposal').hidden&&!document.querySelector('#canvas-chat-send').disabled")
        assert page.evaluate("(()=>{const e=canvasEditor.api.getSceneElements();return e.find(x=>x.type==='line').y-e.find(x=>x.type==='image').y})()")>390
        chat_send('Отмени')
        # A previous radio selection must not affect a later single-match command.
        chat_send('Обведи «Марк»');chat_send('Отмени')
        chat_send('Подчеркни «Дело было ночью»')
        page.locator('#canvas-chat-candidates input[value="all"]').check();page.locator('#canvas-chat-apply').click()
        page.wait_for_function("document.querySelector('#canvas-chat-proposal').hidden&&!document.querySelector('#canvas-chat-send').disabled")
        assert page.evaluate("canvasEditor.api.getSceneElements().filter(e=>e.type==='line').length")==2, page.evaluate("({elements:canvasEditor.api.getSceneElements().map(e=>({type:e.type,id:e.id})),log:document.querySelector('#canvas-chat-log').textContent})")
        chat_send('Удали «Я спросил"')
        assert page.locator('#canvas-chat-candidates input').count()==1
        assert not page.evaluate("canvasEditor.api.getSceneElements().some(e=>e.customData?.dewiarTextCover)")
        page.locator('#canvas-chat-apply').click();page.wait_for_function("document.querySelector('#canvas-chat-proposal').hidden&&!document.querySelector('#canvas-chat-send').disabled")
        mask=page.evaluate("canvasEditor.api.getSceneElements().find(e=>e.customData?.dewiarTextCover)")
        assert mask['backgroundColor']=='#ffffff' and mask['fillStyle']=='solid' and mask['opacity']==100
        exported_result=page.evaluate("canvasEditor.output('canvas_save_as')")
        with Image.open(exported_result['path']) as im:
            # Export has 20px padding; use the known raster origin from the scene.
            im.save(artifacts/('ocr-fixed-'+kind+'.png'))
            # The target lettering is white, while the next phrase stays black.
            pad=(im.width-Image.open(fixture_path).width)//2
            assert im.convert('RGB').crop((150+pad,780+pad,380+pad,820+pad)).getextrema()==((255,255),)*3
            assert im.convert('RGB').crop((400+pad,780+pad,600+pad,815+pad)).getextrema()[0][0]<80
        chat_send('Отмени');assert not page.evaluate("canvasEditor.api.getSceneElements().some(e=>e.customData?.dewiarTextCover)")
        chat_send('Верни');assert page.evaluate("canvasEditor.api.getSceneElements().some(e=>e.customData?.dewiarTextCover)")
        page.reload();page.wait_for_function('window.canvasEditor')
        assert page.evaluate("canvasEditor.api.getSceneElements().some(e=>e.customData?.dewiarTextCover)")
        assert Path(fixture_path).read_bytes()==original
        page.screenshot(path=str(artifacts/('ocr-chat-'+kind+'.png')))
        print('PASS: real Russian OCR fixture; two phrases/all; unrelated selection; mixed quotes delete; white PNG pixels/neighbor intact; undo/redo/reload; original unchanged',flush=True)
    # Main assistant input reaches the editor without invoking the general answer.
    # Real canvas pointer hit testing, multi-selection and exact RGB.
    page.locator('#canvas-chat-toggle').click()
    box=page.locator('#canvas-root').bounding_box()
    def draw(x,y):
        page.locator('.excalidraw').click(position={'x':x,'y':y-25});page.keyboard.press('r')
        page.mouse.move(box['x']+x,box['y']+y);page.mouse.down()
        page.mouse.move(box['x']+x+90,box['y']+y+60,steps=8);page.mouse.up()
    draw(310,230);page.wait_for_function("canvasEditor.api.getSceneElements().filter(e=>e.type==='rectangle').length===1")
    page.wait_for_function("canvasEditor.api.getAppState().selectedElementIds[canvasEditor.api.getSceneElements().find(e=>e.type==='rectangle').id]")
    draw(520,230);page.wait_for_function("canvasEditor.api.getSceneElements().filter(e=>e.type==='rectangle').length===2")
    page.wait_for_function("canvasEditor.api.getAppState().selectedElementIds[canvasEditor.api.getSceneElements().filter(e=>e.type==='rectangle').at(-1).id]")
    def click_shape(x,modifiers=()):
        page.locator('.excalidraw').click(position={'x':x,'y':233},modifiers=list(modifiers))
    click_shape(350,('Shift',))
    page.wait_for_function("Object.values(canvasEditor.api.getAppState().selectedElementIds).filter(Boolean).length===2")
    # Use the built-in Excalidraw property button to edit both objects.
    page.locator('label:has([data-testid="strokeWidth-extraBold"])').click()
    page.wait_for_function("canvasEditor.api.getSceneElements().filter(e=>e.type==='rectangle').every(e=>e.strokeWidth===4)")
    page.locator('#canvas-palette summary').click()
    page.locator('#palette-r').fill('12');page.locator('#palette-g').fill('34');page.locator('#palette-b').fill('56')
    assert page.locator('#palette-hex').input_value()=='#0c2238'
    page.locator('#palette-apply').click()
    page.wait_for_function("canvasEditor.api.getSceneElements().filter(e=>e.type==='rectangle').every(e=>e.strokeColor==='#0c2238')")
    page.locator('#palette-set').select_option('Пастельная')
    page.get_by_role('button',name='Цвет #ffc9c9',exact=True).click()
    page.wait_for_function("canvasEditor.api.getSceneElements().filter(e=>e.type==='rectangle').every(e=>e.strokeColor==='#ffc9c9')")
    page.locator('#canvas-palette summary').click()
    click_shape(350)
    page.wait_for_function("Object.values(canvasEditor.api.getAppState().selectedElementIds).filter(Boolean).length===1")
    click_shape(560)
    page.wait_for_function("Object.values(canvasEditor.api.getAppState().selectedElementIds).filter(Boolean).length===0")
    page.locator('#canvas-chat-toggle').click();page.evaluate('canvasEditor.flush()')
    objects=command('canvas_chat_state',{'canvas_id':page.evaluate('canvasEditor.doc.id')})['objects']
    nums=[r['number'] for r in objects if r['type']=='rectangle'];assert len(nums)==2
    chat_send(f'Выбери #{nums[0]}')
    chat_send(f'Добавь #{nums[1]} к выделению')
    assert page.evaluate('Object.values(canvasEditor.api.getAppState().selectedElementIds).filter(Boolean).length')==2
    chat_send(f'Сними выделение с #{nums[0]}')
    assert page.evaluate('Object.values(canvasEditor.api.getAppState().selectedElementIds).filter(Boolean).length')==1
    chat_send('Сними выделение')
    assert page.evaluate('Object.values(canvasEditor.api.getAppState().selectedElementIds).filter(Boolean).length')==0
    chat_send('Добавь текст «Первая подпись» в точке 320, 360')
    chat_send('Добавь текст «Вторая подпись» в точке 520, 360')
    rows=command('canvas_chat_state',{'canvas_id':page.evaluate('canvasEditor.doc.id')})['objects']
    texts=[r for r in rows if r['type']=='text']
    assert len(texts)==2 and page.evaluate('(id)=>canvasEditor.api.getAppState().selectedElementIds[id]',texts[-1]['id'])
    chat_send('Выбери '+' и '.join(f"#{r['number']}" for r in texts))
    page.locator('label:has([data-testid="fontSize-large"])').click()
    page.wait_for_function("canvasEditor.api.getSceneElements().filter(e=>e.type==='text').every(e=>e.fontSize>24)")
    page.locator('.excalidraw').click(position={'x':760,'y':400})
    page.wait_for_function("!Object.values(canvasEditor.api.getAppState().selectedElementIds).some(Boolean)")
    # Brush finalizes with the stroke selected, so its width can change immediately.
    page.keyboard.press('p')
    root=page.locator('.excalidraw').bounding_box()
    page.mouse.move(root['x']+320,root['y']+490);page.mouse.down()
    page.mouse.move(root['x']+460,root['y']+490,steps=12);page.mouse.up()
    page.wait_for_function("canvasEditor.api.getSceneElements().some(e=>e.type==='freedraw'&&canvasEditor.api.getAppState().selectedElementIds[e.id])")
    page.locator('.excalidraw').click(position={'x':390,'y':520})
    page.wait_for_function("!Object.values(canvasEditor.api.getAppState().selectedElementIds).some(Boolean)")
    page.locator('.excalidraw').click(position={'x':390,'y':492})
    page.wait_for_function("canvasEditor.api.getSceneElements().some(e=>e.type==='freedraw'&&canvasEditor.api.getAppState().selectedElementIds[e.id])")
    print('PASS: latest shape selected; shift multi-select; native stroke width; RGB and palette; repeat-click deselect; chat selection',flush=True)
    scope_id=page.evaluate('canvasEditor.doc.id')
    command('canvas_scope_active',{'canvas_id':scope_id})
    command('canvas_scope_mode',{'mode':'follow'})
    command('canvas_scope_submit',{'text':'Добавь текст «Из основного окна» в точке 20, 30'})
    page.wait_for_function("canvasEditor.api.getSceneElements().some(e=>e.text==='Из основного окна')",timeout=15000)
    page.wait_for_function("!document.querySelector('#canvas-chat-send').disabled")
    assert not command('canvas_scope_state',{})['busy']
    command('canvas_scope_mode',{'mode':'general'})
    command('canvas_scope_active',{'canvas_id':scope_id})
    assert command('canvas_scope_state',{})['canvas_id'] is None
    command('canvas_scope_mode',{'mode':'follow'})
    print('PASS: main assistant scope relay; same canvas history; explicit general mode survives heartbeat',flush=True)
    page.goto(f'http://127.0.0.1:{server.server_port}/canvas.html?id={identifier}&token=canvas-test&port={port}')
    try:
        page.wait_for_function('window.canvasEditor',timeout=30000)
        box=page.locator('#canvas-root').bounding_box()
        page.locator('.excalidraw').click(position={'x':350,'y':250});page.keyboard.press('r')
        page.mouse.move(box['x']+350,box['y']+250);page.mouse.down();page.mouse.move(box['x']+500,box['y']+350,steps=12);page.mouse.up()
        page.wait_for_function('canvasEditor.api.getSceneElements().length===1')
        page.keyboard.press('Meta+z');page.wait_for_function('canvasEditor.api.getSceneElements().length===0')
        page.get_by_role('button',name='Стиль «Инструкция»').click();page.get_by_role('button',name='Следующий номер').click()
        page.evaluate('canvasEditor.flush()');assert page.evaluate('canvasEditor.api.getSceneElements().length')==2
        page.evaluate("canvasEditor.api.updateLibrary({libraryItems:[{id:'test-library',status:'unpublished',created:Date.now(),elements:canvasEditor.api.getSceneElements()}]})")
        page.wait_for_function('canvasEditor.library.length===1');page.evaluate('canvasEditor.flush()')
        page.locator('#image-files').set_input_files([str(large),str(small)])
        page.wait_for_function("document.querySelector('#status').textContent==='Изображения добавлены без уменьшения'",timeout=90000)
        state=page.evaluate('canvasEditor.api.getSceneElements()');assert len([e for e in state if e['type']=='image'])==2
        assert any(e['type']=='image' and e['width']==3000 and e['height']==2000 for e in state)
        page.get_by_role('button',name='Схема Mermaid',exact=True).click()
        page.locator('#diagram-source').fill('flowchart LR\n A[Начало] --> B{Проверка}\n B --> C[Готово]')
        page.locator('#diagram-apply').click();page.wait_for_function("!document.querySelector('#diagram-dialog').open",timeout=45000)
        state=page.evaluate('canvasEditor.api.getSceneElements()');assert any(e['type']=='arrow' for e in state)
        assert any(e['type']=='text' and 'Начало' in e.get('text','') for e in state)
        page.evaluate('canvasEditor.flush()');count=len(state);page.reload();page.wait_for_function('window.canvasEditor',timeout=30000)
        assert page.evaluate('canvasEditor.api.getSceneElements().length')==count
        assert page.evaluate('canvasEditor.library.length')==1
        page.get_by_role('button',name='Следующий номер').click();assert page.evaluate('Math.max(...canvasEditor.api.getSceneElements().map(e=>e.customData?.dewiarNumber||0))')==2
        # Updating Mermaid preserves unrelated images and source text survives reload.
        page.locator('#mermaid').click();page.locator('#diagram-block').select_option(index=1);assert 'Начало' in page.locator('#diagram-source').input_value()
        page.locator('#diagram-source').fill('flowchart LR\n A[Обновлено] --> B[Готово]');page.locator('#diagram-apply').click();page.wait_for_function("!document.querySelector('#diagram-dialog').open",timeout=45000)
        assert page.evaluate("canvasEditor.api.getSceneElements().filter(e=>e.type==='image').length")==2
        page.get_by_role('button',name='Сохранить PNG',exact=True).click();page.wait_for_function("document.querySelector('#status').textContent.startsWith('Готово:')",timeout=90000)
        folder,current=C.read(engine,identifier)
        with Image.open(C.exported(folder,current)) as im:assert im.width>=3000 and im.height>=2000
        page.get_by_role('button',name='Скопировать PNG',exact=True).click();page.wait_for_function("document.querySelector('#status').textContent==='PNG скопирован'",timeout=90000)
        page.locator('#document').click();page.wait_for_function("document.querySelector('#status').textContent.startsWith('Редактируемый исходник сохранён:')")
        exported=ws.safe('Изображения/Холст-'+identifier[:8]+'.excalidraw');data=json.loads(exported.read_text());assert len(data['files'])==2 and data['type']=='excalidraw'
        page.locator('#note').click();page.wait_for_function("document.querySelector('#status').textContent.startsWith('Готово:')",timeout=90000)
        assert list(ws.root.glob('Заметки/Холсты/*.md'))
        page.locator('#task').click();page.locator('#task-target').select_option(task.id);page.locator('#task-send').click()
        page.wait_for_function("document.querySelector('#status').textContent.startsWith('Готово:')",timeout=90000)
        engine.tasks.tell.assert_called_once()
        # Navigation and native close cannot interrupt an in-flight source import.
        page.evaluate('''()=>{const original=core.call.bind(core);core.call=async(cmd,args)=>{if(cmd==='canvas_create'){await new Promise(resolve=>window.releaseImport=resolve);}return original(cmd,args)}}''')
        page.locator('#document-file').set_input_files(str(exported))
        page.wait_for_function('!!window.releaseImport')
        assert page.locator('#new').is_disabled() and page.locator('#documents').is_disabled()
        assert page.evaluate('canvasRequestClose()') is False
        page.evaluate('releaseImport()')
        page.wait_for_function('(id)=>new URLSearchParams(location.search).get("id")!==id&&window.canvasEditor?.doc.id===new URLSearchParams(location.search).get("id")',arg=identifier,timeout=90000)
        assert page.evaluate("canvasEditor.api.getSceneElements().filter(e=>e.type==='image').length")==2
        page.set_viewport_size({'width':640,'height':800});page.screenshot(path=str(artifacts/('canvas-'+kind+'.png')));assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        assert not external,external[:3]
        assert max(frames)<1_000_000,max(frames)
        print('PASS:',kind,'real Bridge; >16 MiB original in chunks; full size; multiple images; Mermaid editable/update; reopen; numbering; PNG/private clipboard; source; 640px; offline assets; largest frame',max(frames))
    except Exception:
        print('UI status:',page.locator('#status').inner_text(),'Errors:',errors,'External:',external[:3]);print('Bridge:',bridge.busy());print('Browser:',page.evaluate('({ready:document.readyState,connected:core.connected,pending:Object.keys(core.pending),hello:!!core.snapshot,scripts:[...document.scripts].map(s=>s.src)})'))
        page.screenshot(path=str(artifacts/('failed-'+kind+'.png')));raise
    finally:browser.close()
server.shutdown();server.server_close();bridge.pool.shutdown(wait=True);TEMP.cleanup()
