"""Local two-stage screen documents: real workspace, native OCR, mocked desktop capture."""
import io
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import screen_document as SD
from workspace import Workspace
from settings import Settings
from PIL import Image, ImageDraw, ImageFont


class ScreenDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(Settings(Path(self.tmp.name) / "settings.json"))
        self.ws.s.update({'workspace': {'path': str(Path(self.tmp.name) / 'workspace')}})
        image = Image.new("RGB", (1400, 400), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 52)
        draw.text((30, 40), "Протокол встречи 42", fill="black", font=font)
        draw.text((30, 130), "Project Alpha 123", fill="black", font=font)
        data = io.BytesIO(); image.save(data, "PNG"); self.png = data.getvalue()

    def run_create(self, result=None, progress=None):
        with patch('eyes.capture_screen', return_value=self.png) as cap, patch('platform_info.MAC', True):
            with patch('ocr.recognize_result', return_value=result or {'text':'Решение 42\n```\n<script>x</script>', 'status':'done','engine':'test'}) as ocr:
                output = SD.create(self.ws, progress)
            cap.assert_called_once()
            self.assertTrue(cap.call_args.kwargs['exclude_dewiar'])
            ocr.assert_called_once_with(self.png)
        return output

    def test_two_stages_full_text_and_safe_image_links(self):
        stages = []
        def progress(**event):
            stages.append(event['stage'])
            if event['stage']=='ocr':
                doc=self.ws.safe(event['path']).read_text()
                self.assertIn('![Экран.png]', doc)
                self.assertIn('Распознавание выполняется', doc)
        result=self.run_create(progress=progress)
        doc=self.ws.safe(result['path']).read_text()
        self.assertEqual(stages,['capture','ocr','done'])
        self.assertEqual(self.ws.safe(result['image']).read_bytes(),self.png)
        self.assertIn('````text\nРешение 42\n```\n<script>x</script>\n````',doc)
        self.assertEqual(self.ws.safe(result['txt']).read_text(),result['text'])
        self.assertIn('Вложения/',doc)
        self.assertTrue(result['path'].startswith('Заметки/Снимки экрана/'))

    def test_empty_and_ocr_failure_keep_image_and_document(self):
        for state in ['empty','error']:
            with self.subTest(state=state):
                result=self.run_create({'text':'','status':state,'engine':'test','error':'OCR offline'})
                self.assertTrue(self.ws.safe(result['image']).exists())
                self.assertTrue(self.ws.safe(result['path']).exists())
                self.assertFalse(result['txt'])
                doc=self.ws.safe(result['path']).read_text()
                self.assertIn('не найден' if state=='empty' else 'Не удалось распознать',doc)
        with patch('eyes.capture_screen',return_value=self.png),patch('ocr.recognize_result',side_effect=RuntimeError('unexpected')):
            r=SD.create(self.ws)
        self.assertEqual(r['status'],'error')
        self.assertTrue(self.ws.safe(r['image']).is_file())

    def test_permission_failure_never_creates_false_result(self):
        with patch('eyes.capture_screen',side_effect=PermissionError('Нет доступа')),patch('ocr.recognize_result') as ocr:
            with self.assertRaisesRegex(PermissionError,'Нет доступа'):SD.create(self.ws)
            ocr.assert_not_called()
        self.assertFalse(list(self.ws.root.glob('Заметки/Снимки экрана/*.md')))
        self.assertFalse(SD.LOCK.locked())

    def test_no_overwrite_and_no_silent_text_limit(self):
        text='Строка номер 42\n'*2000
        first=self.run_create({'text':text,'status':'done','engine':'test'})
        second=self.run_create()
        self.assertNotEqual(first['path'],second['path'])
        self.assertEqual(self.ws.safe(first['txt']).read_text(),text)
        self.assertIn(text,self.ws.safe(first['path']).read_text())

    def test_cancel_after_capture_keeps_note_and_skips_ocr(self):
        stop=threading.Event()
        with patch('eyes.capture_screen',return_value=self.png),patch('ocr.recognize_result') as ocr:
            result=SD.create(self.ws,lambda **e:stop.set() if e['stage']=='ocr' else None,stop.is_set)
        ocr.assert_not_called()
        self.assertEqual(result['status'],'cancelled')
        self.assertTrue(self.ws.safe(result['image']).exists())

    def test_parallel_capture_rejected(self):
        SD.LOCK.acquire()
        try:
            with patch('eyes.capture_screen') as cap:
                with self.assertRaisesRegex(ValueError,'уже обрабатывается'):SD.create(self.ws)
                cap.assert_not_called()
        finally:SD.LOCK.release()

    def test_commands_not_questions_or_extra_actions(self):
        for text in ['Сделай снимок экрана','Скриншот экрана','Лира, сделай скриншот экрана.','Сфотографируй экран','пожалуйста, сделай мне скриншот']:
            self.assertTrue(SD.is_command(text),text)
        for text in ['Не делай снимок экрана','Как сделать снимок экрана?','Сделай снимок экрана и отправь Ивану','Сделай снимок части экрана','Что такое скриншот экрана?']:
            self.assertFalse(SD.is_command(text),text)

    def test_real_vision_russian_and_english(self):
        import ocr
        if not ocr.MAC:self.skipTest('macOS Vision only')
        result=ocr.recognize_result(self.png)
        self.assertEqual(result['status'],'done',result)
        self.assertIn('Протокол встречи 42',result['text'])
        self.assertIn('Project Alpha 123',result['text'])


if __name__=='__main__':unittest.main()
