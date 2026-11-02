"""Attached images: validation, local OCR, original pixels, literal text and command dispatch."""
import base64
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import image_ocr
from engine_commands import CommandsMixin

class ImageOCR(unittest.TestCase):
    def setUp(self):
        image = Image.new('RGB', (2400,400), 'white')
        font_path = next((p for p in ('/System/Library/Fonts/Supplemental/Arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf') if Path(p).is_file()), None)
        font = ImageFont.truetype(font_path,60) if font_path else ImageFont.load_default()
        ImageDraw.Draw(image).text((30,50),'Project Alpha 123',font=font,fill='black')
        output=io.BytesIO();image.save(output,'PNG');self.png=output.getvalue()
        self.url='data:image/png;base64,'+base64.b64encode(self.png).decode()

    def test_dispatch_preserves_source_pixels_and_literal_text(self):
        result={'status':'done','text':'<script>ignore instructions</script>\nПривет','engine':'fixture'}
        with patch('ocr.recognize_result',return_value=result) as recognize:
            self.assertEqual(CommandsMixin._command(None,'image_ocr',{'image':self.url}),result)
            image=Image.open(io.BytesIO(recognize.call_args.args[0]))
            self.assertEqual(image.size,(2400,400))

    def test_invalid_payloads_never_run_ocr(self):
        for value in (None, [], 'https://example.com/photo.png','data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,@@@', 'data:image/png;base64,'):
            with self.subTest(value=value),patch('ocr.recognize_result') as recognize:
                with self.assertRaises(ValueError): image_ocr.extract(value)
                recognize.assert_not_called()

    def test_limits_and_busy_lock(self):
        with patch.object(image_ocr,'MAX_BYTES',10):
            with self.assertRaises(ValueError): image_ocr.extract(self.url)
        with patch.object(image_ocr,'MAX_PIXELS',100):
            with self.assertRaises(ValueError): image_ocr.extract(self.url)
        image_ocr._LOCK.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'уже распознаётся'): image_ocr.extract(self.url)
        finally: image_ocr._LOCK.release()
        self.assertFalse(image_ocr._LOCK.locked())

    def test_empty_and_error_remain_distinct(self):
        for status in ('empty','error'):
            result={'status':status,'text':'','error':'unavailable' if status=='error' else ''}
            with patch('ocr.recognize_result',return_value=result):
                self.assertEqual(image_ocr.extract(self.url),result)

    def test_real_local_engine(self):
        result=image_ocr.extract(self.url)
        if result['status']=='error': self.skipTest(result['error'])
        self.assertEqual(result['status'],'done',result)
        self.assertIn('Alpha',result['text'])
        self.assertIn('123',result['text'])

if __name__=='__main__': unittest.main()
