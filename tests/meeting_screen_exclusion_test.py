"""Meeting frames exclude Dewiar and crop retina regions in display coordinates."""
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import macsys

class Capture(unittest.TestCase):
    def test_region_uses_clean_pixels_and_retina_scale(self):
        image = Image.new('RGB', (200, 100), 'green')
        for x in range(20, 60):
            for y in range(10, 30): image.putpixel((x, y), (220, 0, 0))
        buf = io.BytesIO(); image.save(buf, 'PNG')
        bounds = SimpleNamespace(origin=SimpleNamespace(x=0, y=0), size=SimpleNamespace(width=100, height=50))
        with patch('Quartz.CGPreflightScreenCaptureAccess', return_value=True), patch('Quartz.CGMainDisplayID', return_value=1), patch('Quartz.CGDisplayBounds', return_value=bounds), patch.object(macsys, '_screenshot_without_dewiar', return_value=buf.getvalue()) as capture:
            raw = macsys.screen_capture('region', {'x':10, 'y':5, 'width':20, 'height':10}, exclude_dewiar=True)
            result = Image.open(io.BytesIO(raw))
            self.assertEqual(result.size, (40, 20))
            self.assertEqual(result.getpixel((5, 5)), (220, 0, 0))
            capture.assert_called_once()
            with self.assertRaises(ValueError): macsys.screen_capture('region', {'x':90, 'y':0, 'width':20, 'height':10}, exclude_dewiar=True)

if __name__ == '__main__': unittest.main()
