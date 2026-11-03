"""Visible windows, exact application matching and private region preview lifecycle."""
import io
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import screenshot_sources as sources
import screenshot_messages as messages
from workspace import Workspace
from settings import Settings
from chatlog import ChatLog
from screen_document import parse_command


class ScreenshotSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        settings=Settings(root/'settings.json');settings.update({'workspace':{'path':str(root/'workspace')}})
        self.engine=SimpleNamespace(s=settings,workspace=Workspace(settings),chatlog=ChatLog(root/'chat.jsonl'),
                                    toolbox=SimpleNamespace(policy=Mock(return_value=(True,False))),note=Mock(),emit=Mock())
        out=io.BytesIO();Image.new('RGB',(600,400),(11,22,33)).save(out,'PNG');self.png=out.getvalue()

    def test_window_thumbnail_and_full_preview_do_not_save_or_ocr(self):
        from engine_commands import CommandsMixin
        with patch('screenshot_sources.capture_window',return_value=self.png) as capture, patch('ocr.recognize_result') as ocr:
            thumb=CommandsMixin.command(self.engine,'screenshot_window_preview',{'window_id':42})
            full=CommandsMixin.command(self.engine,'screenshot_window_preview',{'window_id':42,'full_size':True})
            self.assertEqual(Image.open(io.BytesIO(base64.b64decode(thumb.split(',')[1]))).size,(300,200))
            original=Image.open(io.BytesIO(base64.b64decode(full.split(',')[1])))
            self.assertEqual(original.format,'PNG');self.assertEqual(original.size,(600,400))
            self.assertEqual(original.getpixel((0,0)),(11,22,33))
            self.assertEqual(capture.call_args.kwargs,{'window_id':42});ocr.assert_not_called()
            self.assertFalse(self.engine.chatlog.page()['items'])
            self.assertFalse(list(self.engine.workspace.root.glob('Входящие/Снимки экрана/*')))
            self.engine.toolbox.policy.return_value=(False,False)
            with self.assertRaises(ValueError):CommandsMixin.command(self.engine,'screenshot_window_preview',{'window_id':42,'full_size':True})
            self.assertEqual(capture.call_count,2)

    def test_region_preview_cancel_and_confirm(self):
        with patch('eyes.capture_screen',return_value=self.png),patch('ocr.recognize_result') as ocr:
            token=messages.prepare_region(self.engine)['id']
            self.assertFalse(self.engine.chatlog.page()['items']);ocr.assert_not_called()
            self.assertFalse(list(self.engine.workspace.root.glob('Входящие/Снимки экрана/*')))
            self.assertEqual(messages.draft(self.engine,token)['width'],600)
            with self.assertRaises(ValueError):messages.draft(self.engine,token,{'x':590,'y':0,'width':100,'height':100})
            ocr.return_value={'status':'empty','text':'','engine':'fixture'}
            result=messages.capture(self.engine,draft_id=token,region={'x':30,'y':40,'width':80,'height':90})
            image=Image.open(self.engine.workspace.safe(result['image']))
            self.assertEqual(image.size,(80,90));self.assertEqual(image.getpixel((0,0)),(11,22,33));ocr.assert_called_once()
            with self.assertRaises(ValueError):messages.draft(self.engine,token)

    def test_stale_other_chat_and_disabled_previews(self):
        with patch('eyes.capture_screen',return_value=self.png):
            token=messages.prepare_region(self.engine)['id']
            self.engine._screenshot_drafts[token]['created']-=601
            with self.assertRaisesRegex(ValueError,'устарел'):messages.draft(self.engine,token)
            token=messages.prepare_region(self.engine)['id'];self.engine.chatlog.active='different'
            with self.assertRaisesRegex(ValueError,'Чат изменился'):messages.draft(self.engine,token)
        self.engine.toolbox.policy.return_value=(False,False)
        with patch('eyes.capture_screen') as capture:
            with self.assertRaises(ValueError):messages.prepare_region(self.engine)
            capture.assert_not_called()

    def test_named_commands_are_anchored(self):
        for text in ['Снимок экрана приложения Клод','Лира, сделай скриншот программы Кодекс.','Сделай снимок экрана окна Safari']:
            self.assertEqual(parse_command(text)['source'],'application')
        self.assertEqual(parse_command('Снимок экрана приложения Клод')['application'],'Клод')
        for text in ['Как сделать снимок экрана приложения Safari?', 'Не делай снимок экрана приложения Safari', 'Снимок экрана приложения']:
            self.assertIsNone(parse_command(text))

    def test_exact_alias_and_ambiguous_window_selection(self):
        rows=[{'window_id':1,'name':'ChatGPT','id':'com.openai.codex','title':'Проект'},
              {'window_id':2,'name':'Xcode','id':'xcode','title':'Код'}]
        self.assertEqual(sources.choose_window(rows,'кодекс')['window_id'],1)
        with self.assertRaises(ValueError):sources.choose_window(rows,'Code')
        with self.assertRaisesRegex(ValueError,'несколько'):sources.choose_window(rows+[dict(rows[0],window_id=3)],'кодекс')
        self.assertEqual(sources.choose_window(rows,window_id=2)['name'],'Xcode')
        with self.assertRaisesRegex(ValueError,'не относится'):sources.choose_window(rows,'кодекс',window_id=2)
        for invalid in [False,0,-1,'1',999]:
            with self.assertRaises(ValueError):sources.choose_window(rows,window_id=invalid)

    def test_capture_revalidates_window_and_uses_fixed_argv(self):
        row={'window_id':9,'name':'Fixture','id':'fixture','title':'Окно'}
        def run(argv,**kwargs):Path(argv[-1]).write_bytes(self.png);return SimpleNamespace(returncode=0)
        with patch.object(sources,'visible_windows',return_value=[row]),patch.object(sources.subprocess,'run',side_effect=run) as capture:
            self.assertEqual(sources.capture_window(window_id=9),self.png)
            self.assertEqual(capture.call_args.args[0][:6],['/usr/sbin/screencapture','-l','9','-x','-o','-t'])
            self.assertNotIn('shell',capture.call_args.kwargs)
        with patch.object(sources,'visible_windows',return_value=[]),patch.object(sources.subprocess,'run') as capture:
            with self.assertRaises(ValueError):sources.capture_window(window_id=9)
            capture.assert_not_called()

    @unittest.skipUnless(sys.platform=='darwin','desktop window contract')
    def test_large_window_does_not_resize_hud_or_accept_arbitrary_pages(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
        import app
        api=app.Api('test-token');api._hud=Mock()
        with patch.object(api,'_url',return_value='http://127.0.0.1/fixture'),patch('webview.create_window',return_value=MagicMock()) as create:
            self.assertTrue(api.open_screenshot_window('view','abc123'))
            self.assertEqual(create.call_args.kwargs['width'],1100)
            window=api._screenshot_window
            self.assertTrue(api.open_screenshot_window('region','abc456'))
            create.assert_called_once();window.load_url.assert_called_once()
            api._hud.resize.assert_not_called()
            self.assertTrue(api.open_screenshot_window('window','42'))
            self.assertEqual(api._url.call_args.args,('screenshot-window.html','&mode=window&id=42'))
            for invalid in ['0','-1','abc','../../etc/passwd']:
                with self.assertRaises(ValueError):api.open_screenshot_window('window',invalid)
            with self.assertRaises(ValueError):api.open_screenshot_window('file','../../etc/passwd')
            api.close_screenshot_window();window.destroy.assert_called_once()

    @unittest.skipUnless(sys.platform=='darwin','macOS window metadata')
    def test_hidden_covered_overlay_and_own_windows_are_filtered(self):
        raw=[
            {'kCGWindowNumber':1,'kCGWindowOwnerName':'Front','kCGWindowBounds':{'X':0,'Y':0,'Width':100,'Height':100}},
            {'kCGWindowNumber':2,'kCGWindowOwnerName':'Covered','kCGWindowBounds':{'X':10,'Y':10,'Width':80,'Height':80}},
            {'kCGWindowNumber':3,'kCGWindowOwnerName':'Side','kCGWindowBounds':{'X':100,'Y':0,'Width':100,'Height':100}},
            {'kCGWindowNumber':4,'kCGWindowOwnerName':'Dewiar','kCGWindowBounds':{'X':200,'Y':0,'Width':100,'Height':100}},
            {'kCGWindowNumber':5,'kCGWindowOwnerName':'Hidden','kCGWindowIsOnscreen':False},
            {'kCGWindowNumber':6,'kCGWindowOwnerName':'Overlay','kCGWindowLayer':3},
        ]
        with patch('Quartz.CGPreflightScreenCaptureAccess',return_value=True),patch('Quartz.CGWindowListCopyWindowInfo',return_value=raw), \
             patch('AppKit.NSWorkspace') as workspace:
            workspace.sharedWorkspace.return_value.runningApplications.return_value=[]
            self.assertEqual([r['window_id'] for r in sources.visible_windows()],[1,3])


if __name__=='__main__':unittest.main()
