"""The Core Audio tap input format must not replace the output PCM format."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import audio_io


class TapFormatTest(unittest.TestCase):
    def test_native_tap_rate_is_not_used_as_recorded_rate(self):
        tap = audio_io.TapCapture.__new__(audio_io.TapCapture)
        tap.rate, tap.channels = 16000, 1
        tap.events = []
        tap.warning = tap.error = tap.status = tap.state = ""
        tap._ready = threading.Event()
        with patch.object(audio_io.log, 'info'):
            class Stream:
                rows = iter([json.dumps({'ev':'tap','rate':48000,'channels':2}).encode()+b'\n', b''])
                def readline(self): return next(self.rows)
            audio_io.TapCapture._stderr(tap, type('Proc', (), {'stderr': Stream()})())
        self.assertEqual((tap.rate, tap.channels), (16000, 1))

    def test_ready_event_selects_output_format(self):
        tap = audio_io.TapCapture.__new__(audio_io.TapCapture)
        tap.rate, tap.channels = 16000, 1
        tap.events = []
        tap.warning = tap.error = tap.status = tap.state = ""
        tap._ready = threading.Event()
        class Stream:
            rows = iter([json.dumps({'ev':'ready','rate':16000,'channels':1}).encode()+b'\n', b''])
            def readline(self): return next(self.rows)
        audio_io.TapCapture._stderr(tap, type('Proc', (), {'stderr': Stream()})())
        self.assertEqual((tap.rate, tap.channels), (16000, 1))


if __name__ == '__main__':
    unittest.main()
