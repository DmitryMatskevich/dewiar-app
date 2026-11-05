"""Isolated command/export and actual editor UI acceptance. No LLM, mic or account changes."""
import base64,copy,io,json,sys,tempfile,functools,http.server,threading,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image,ImageDraw,ImageFont
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'core'))
from settings import Settings
from workspace import Workspace
from chatlog import ChatLog
from engine_commands import CommandsMixin
import visual_assets as V
from visual_compose import png_chunks
with tempfile.TemporaryDirectory(prefix='dewiar-visual-') as td:
    temp=Path(td);settings=Settings(temp/'settings.json');settings.update({'workspace':{'path':str(temp/'workspace')}})
    ws=Workspace(settings);log=ChatLog(temp/'chat.jsonl');events=[]
    engine=SimpleNamespace(workspace=ws,chatlog=log,s=settings,emit=lambda *a,**kw:events.append((a,kw)))
    src=ws.safe('Входящие/Снимки экрана/test.png');src.parent.mkdir(parents=True,exist_ok=True)
    image=Image.new('RGB',(1600,900),'white')
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',32)
    for text,xy in [('Сохранить проект',(900,60)),('Сохранить',(950,300)),('Итого 123',(1000,500)),('test@example.com',(900,650)),('пароль: test-only',(50,650))]:ImageDraw.Draw(image).text(xy,text,font=font,fill='black')
    image.save(src);original=src.read_bytes()
    item=log.add('assistant','ТЕСТ — отдельные данные',att=[{'kind':'image','name':'ТЕСТ','path':str(src.relative_to(ws.root)),'thumb':'','capture':{'text':'secret','status':'done','engine':'test','summary':''}}]);id=item['id']
    task=SimpleNamespace(id='test-task',dir=temp/'task');task.dir.mkdir()
    engine.tasks=SimpleNamespace(list=lambda:[{'id':task.id,'title':'ТЕСТ задача'}],get=lambda tid:task,
        create=Mock(return_value=task),tell=Mock())
    def command(cmd,args):
        if cmd=='visual_dictation_start':return {'ok':True,'session':42}
        if cmd in ('visual_dictation_finish','visual_dictation_cancel'):return {'ok':True}
        if cmd=='visual_copy':
            _,_,folder,doc=V.read(engine,id);p=V.exported(folder,doc)
            from AppKit import NSPasteboard
            from macsys import copy_png_file
            board=NSPasteboard.pasteboardWithUniqueName();copy_png_file(p,board)
            assert bytes(board.dataForType_('public.png'))==p.read_bytes()
            board.releaseGlobally();return True
        return CommandsMixin.command(engine,cmd,args)
    d=command('visual_open',{'asset_id':id});s=copy.deepcopy(d['state'])
    s['markers']=[{'typeName':'CoverMarker','left':40,'top':40,'width':100,'height':50,'rotationAngle':30,'fillColor':'#ff000000','opacity':.1}]
    command('visual_save',{'asset_id':id,'base_revision':0,'state':s,'crop':None})
    try:command('visual_save',{'asset_id':id,'base_revision':0,'state':s});raise AssertionError('conflict accepted')
    except ValueError:pass
    bad=copy.deepcopy(s);bad['markers'][0]['left']=float('nan')
    try:V.validate(bad,d['source'],None);raise AssertionError('NaN accepted')
    except ValueError:pass
    overlay=io.BytesIO();Image.new('RGBA',(1600,900)).save(overlay,'PNG')
    command('visual_publish',{'asset_id':id,'revision':1,'overlay':'data:image/png;base64,'+base64.b64encode(overlay.getvalue()).decode()})
    _,_,folder,doc=V.read(engine,id);out=V.exported(folder,doc)
    with Image.open(out) as im:assert im.getpixel((90,65))==(0,0,0);assert im.getpixel((500,500))==(255,255,255)
    assert not set(png_chunks(out.read_bytes())) & {'tEXt','iTXt','zTXt','eXIf','iCCP'}
    assert src.read_bytes()==original
    canvas=command('visual_to_canvas',{'asset_id':id})
    import canvases
    cf,cd=canvases.read(engine,canvas['canvas_id'])
    assert cd['sources'][0]['screenshot_id']==id
    with Image.open(cf/next(iter(cd['files'].values()))['path']) as transferred:assert transferred.getpixel((90,65))==(0,0,0,255)
    note=command('visual_to_note',{'asset_id':id});assert 'secret' not in ws.safe(note['path']).read_text()
    assert command('visual_tasks',{'asset_id':id})[0]['id']==task.id
    sent=command('visual_to_task',{'asset_id':id,'task_id':task.id,'text':'ТЕСТ задача'})
    assert Path(sent['path']).read_bytes()==out.read_bytes()
    assert engine.tasks.tell.call_args.args[0]==task.id
    assert 'secret' not in engine.tasks.tell.call_args.args[1]
    command('visual_to_task',{'asset_id':id,'text':'ТЕСТ новая'})
    assert engine.tasks.create.call_args.kwargs['start'] is False
    for rev in range(25):V.checkpoint(folder,{**doc,'revision':rev})
    assert len(list((folder/'history').glob('*.json')))==20
    command('visual_save',{'asset_id':id,'base_revision':1,'state':d['state'],'crop':None})
    try:V.exported(folder,V.read(engine,id)[3]);raise AssertionError('stale export accepted')
    except ValueError:pass
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(ROOT/'ui')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with sync_playwright() as p:
        browser=getattr(p,os.environ.get('DEWIAR_TEST_BROWSER','chromium')).launch()
        page=browser.new_page(viewport={'width':1200,'height':850});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.expose_function('command',command)
        stub="window.core={snapshot:{},handlers:{},on(name,fn){(this.handlers[name]??=[]).push(fn)},emit(name,data){for(const fn of this.handlers[name]||[])fn(data)},call:window.command};window.api=()=>({});"
        page.route('**/assets/dewiar.js',lambda route:route.fulfill(body=stub,content_type='text/javascript'))
        page.goto(f'http://127.0.0.1:{server.server_port}/visual-editor.html?id={id}')
        page.evaluate("document.title='ТЕСТ — отдельные данные';document.querySelector('header strong').textContent='ТЕСТ — отдельные данные'")
        page.wait_for_function('window.visualEditor')
        # Use real toolbar + pointer creation, then persistence.
        page.get_by_role('button',name='Рамка',exact=True).click()
        box=page.locator('#area').bounding_box();page.mouse.move(box['x']+100,box['y']+100);page.mouse.down();page.mouse.move(box['x']+260,box['y']+170,steps=10);page.mouse.up()
        page.wait_for_function('visualEditor.area.getState().markers.length===1')
        page.evaluate('visualEditor.flush()');page.reload();page.wait_for_function('window.visualEditor && visualEditor.area.getState().markers.length===1')
        page.get_by_role('button',name='Номер',exact=True).click();page.wait_for_function('visualEditor.area.getState().markers.length===2')
        page.get_by_role('button',name='Отменить',exact=True).click();page.wait_for_function('visualEditor.area.getState().markers.length===1')
        page.get_by_role('button',name='Повторить',exact=True).click();page.wait_for_function('visualEditor.area.getState().markers.length===2')
        page.get_by_role('button',name='Скопировать PNG',exact=True).click();page.wait_for_function("document.querySelector('#status').textContent==='Скопировано с пометками'",timeout=30000)
        page.get_by_role('button',name='Обрезать…').click();page.locator('#crop-width').fill('800');page.locator('#crop-height').fill('600');page.get_by_role('button',name='Применить',exact=True).click()
        page.get_by_role('button',name='Сохранить копию').click();page.wait_for_function("document.querySelector('#status').textContent.startsWith('Готово:')")
        with Image.open(folder/'export.png') as im:assert im.size==(800,600)
        # Every free drawing tool goes through real pointer gestures and backend validation.
        for label in ['Стрелка','Овал','Маркер','Кисть','Скрыть','Текст']:
            page.get_by_role('button',name=label,exact=True).click()
            box=page.locator('#area').bounding_box()
            page.mouse.move(box['x']+180,box['y']+220);page.mouse.down();page.mouse.move(box['x']+330,box['y']+290,steps=10);page.mouse.up()
            page.get_by_role('button',name='Выбор',exact=True).click()
            page.evaluate('visualEditor.flush()')
        types=page.evaluate('visualEditor.area.getState().markers.map(m=>m.typeName)')
        assert {'ArrowMarker','EllipseFrameMarker','HighlighterMarker','FreehandMarker','CoverMarker','TextMarker'}<=set(types),types
        page.get_by_role('button',name='В задачу…',exact=True).click()
        page.locator('#task-target').select_option(task.id)
        page.locator('#task-text').fill('ТЕСТ отправка снимка')
        page.get_by_role('button',name='Отправить и начать',exact=True).click()
        page.wait_for_function("document.querySelector('#status').textContent.startsWith('Готово:')")
        assert engine.tasks.tell.call_args.args[1].startswith('ТЕСТ отправка снимка')
        page.set_viewport_size({'width':640,'height':780});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ROOT/('work/visual-verification/editor-'+os.environ.get('DEWIAR_TEST_BROWSER','chromium')+'.png')))
        # Scoped dictation events only enter this editor, without microphone use.
        page.locator('#command-input').fill('')
        page.locator('#command-mic').click()
        page.wait_for_function("document.querySelector('#command-mic').textContent==='Остановить диктовку'")
        page.evaluate("core.emit('visual_dictation',{asset_id:'another-image',session:42,state:'partial',text:'wrong target'})")
        assert page.locator('#command-input').input_value()==''
        page.evaluate("core.emit('visual_dictation',{asset_id:visualEditor.doc.asset_id,session:42,state:'partial',text:'подчеркни Итого синим'})")
        assert page.locator('#command-input').input_value()=='подчеркни Итого синим'
        assert page.locator('#command-run').is_disabled()
        page.locator('#command-mic').click()
        page.evaluate("core.emit('visual_dictation',{asset_id:visualEditor.doc.asset_id,session:42,state:'done'})")
        assert page.locator('#command-run').is_enabled()
        # P1b: deterministic geometry fixture, real commands, preview and one-step undo.
        from visual_commands_test import fixture
        from visual_commands import index_result
        V.save(folder/'ocr.json',index_result(fixture(),d['source']))
        page.evaluate('visualEditor.doc.crop=null;visualEditor.flush()')
        count=page.evaluate('visualEditor.area.getState().markers.length')
        page.locator('#command-input').fill('подчеркни «Итого» синим')
        page.locator('#command-run').click()
        page.wait_for_function('(n)=>visualEditor.area.getState().markers.length===n',arg=count+1)
        assert page.evaluate('visualEditor.area.getState().markers.at(-1).typeName')=='LineMarker'
        assert page.evaluate('visualEditor.area.getState().markers.at(-1).strokeColor')=='#1971c2'
        page.get_by_role('button',name='Отменить',exact=True).click()
        page.wait_for_function('(n)=>visualEditor.area.getState().markers.length===n',arg=count)
        page.locator('#command-input').fill('обведи «Сохранить» красным')
        page.locator('#command-run').click();page.wait_for_function("document.querySelector('#command-dialog').open")
        assert page.locator('#command-options input').count()==2
        assert page.locator('#command-apply').is_disabled()
        page.screenshot(path=str(ROOT/('work/visual-p1b/preview-'+os.environ.get('DEWIAR_TEST_BROWSER','chromium')+'.png')))
        page.locator('#command-options input').nth(1).check();page.locator('#command-apply').click()
        page.wait_for_function("!document.querySelector('#command-dialog').open")
        assert page.evaluate('visualEditor.area.getState().markers.at(-1).left')>900
        count=page.evaluate('visualEditor.area.getState().markers.length')
        page.locator('#private-find').click();page.wait_for_function("document.querySelector('#command-dialog').open")
        assert page.locator('#command-options input').count()==2
        page.locator('#command-cancel').click()
        assert page.evaluate('visualEditor.area.getState().markers.length')==count
        page.locator('#private-find').click();page.wait_for_function("document.querySelector('#command-dialog').open")
        page.locator('#command-apply').click();page.wait_for_function("!document.querySelector('#command-dialog').open")
        assert page.evaluate('visualEditor.area.getState().markers.length')==count+2
        page.get_by_role('button',name='Отменить',exact=True).click()
        assert page.evaluate('visualEditor.area.getState().markers.length')==count
        page.evaluate('visualEditor.flush()')
        page.screenshot(path=str(ROOT/('work/visual-verification/commands-'+os.environ.get('DEWIAR_TEST_BROWSER','chromium')+'.png')))
        page.add_script_tag(url=f'http://127.0.0.1:{server.server_port}/assets/screenshot-card.js')
        page.evaluate('''() => {window.api=()=>({open_visual_editor:(kind,id)=>{window.pencilOpened={kind,id}}});const value={id:'test',title:'ТЕСТ',url:'',text:'ТЕСТ',status:'done'};document.body.append(DewiarScreenshots.card(value));document.body.append(DewiarScreenshots.card({...value,id:'readonly'},{readOnly:true}));}''')
        page.locator('[data-screenshot-id=test] [data-ss-action=pencil]').click()
        assert page.evaluate('pencilOpened')=={'kind':'screenshot','id':'test'}
        assert page.locator('[data-screenshot-id=readonly] [data-ss-action=pencil]').is_disabled()
        assert not errors,errors
        browser.close()
    server.shutdown()
print('PASS: revision conflicts, validation, opaque rotated redaction, clean PNG, original, stale export, notes, toolbar drawing, reopen, undo/redo, native private clipboard, crop, 640px')
