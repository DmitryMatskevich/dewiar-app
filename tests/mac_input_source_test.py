"""No settings or desktop changes: probe TIS from a caller without a Cocoa loop."""
import json
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import mac_control as mc


class InputSourceTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS native read-only probe')
    def test_background_caller_without_cocoa_run_loop(self):
        # The main thread waits just like the core server. Dispatching to its
        # NSOperationQueue hangs; direct native calls on a background thread abort.
        with ThreadPoolExecutor(max_workers=1) as pool:
            actual = pool.submit(mc._input_source_worker, 'current').result(timeout=7)
        self.assertTrue(actual['source_id'])
        self.assertIn(actual['language'], ('english', 'russian', 'other'))

    def test_worker_failure_is_reported_and_indicator_remains_optional(self):
        failures = [
            subprocess.CompletedProcess([], -5, '', 'native assertion'),
            subprocess.CompletedProcess([], 1, json.dumps({'ok': False, 'error': 'Раскладка выключена'}), ''),
            subprocess.CompletedProcess([], 0, '[]', ''),
        ]
        for result in failures:
            with self.subTest(result=result), patch.object(mc.subprocess, 'run', return_value=result):
                with self.assertRaises(RuntimeError):
                    mc.set_input_language('Russian')
                self.assertEqual(mc.current_input_language(), '')
        with patch.object(mc.subprocess, 'run', side_effect=subprocess.TimeoutExpired([], 5)):
            with self.assertRaisesRegex(RuntimeError, 'не ответила вовремя'):
                mc.set_input_language('English')
            self.assertEqual(mc.current_input_language(), '')

    def test_invalid_language_never_starts_worker(self):
        with patch.object(mc.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                mc.set_input_language('unexpected')
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
