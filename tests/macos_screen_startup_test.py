"""Screen permission lifecycle across process restarts and rebuilt applications.

Native requests are mocked: this suite never prompts or reads the user's screen.
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'core'), str(ROOT / 'ui')]
import app
import mac_screen_access
from macshell import make_api
from settings import Settings


@unittest.skipUnless(sys.platform == 'darwin', 'Cocoa wrappers')
class ScreenStartupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.settings = self.root / 'settings.json'
        self.settings.write_text('{}')
        self.build = 'build-A'
        for target, kwargs in [
            ('mac_screen_access.STATE_DIR', {'new': self.root}),
            ('mac_screen_access.build_id', {'side_effect': lambda: self.build}),
            ('app.SETTINGS_PATH', {'new': self.settings}),
            ('sys.frozen', {'new': True, 'create': True}),
        ]:
            p = patch(target, **kwargs); p.start(); self.addCleanup(p.stop)
        p = patch('Quartz.CGPreflightScreenCaptureAccess', return_value=False)
        self.preflight = p.start(); self.addCleanup(p.stop)
        p = patch('PyObjCTools.AppHelper.callAfter')
        self.dispatch = p.start(); self.addCleanup(p.stop)
        p = patch('macsys.request_screen_capture_permission', return_value=False)
        self.request = p.start(); self.addCleanup(p.stop)
        self.api = make_api(app)('fixture')
        self.api.open_settings = lambda *args: None

    def test_denial_is_not_reprompted_after_restart_but_new_build_requests(self):
        self.api.startup_screen_access()
        self.api.startup_screen_access()
        self.dispatch.assert_called_once()
        self.request.assert_not_called()
        self.dispatch.call_args.args[0]()
        self.request.assert_called_once()
        self.assertTrue(mac_screen_access.was_requested())
        make_api(app)('fixture').startup_screen_access()
        self.dispatch.assert_called_once()
        self.build = 'build-B'
        make_api(app)('fixture').startup_screen_access()
        self.assertEqual(self.dispatch.call_count, 2)

    def test_existing_grant_suppresses_prompt_even_for_new_build(self):
        self.preflight.return_value = True
        self.api.startup_screen_access()
        self.dispatch.assert_not_called()
        self.assertFalse(mac_screen_access.was_requested())

    def test_denied_startup_opens_setup_outside_cocoa_main_callback(self):
        self.api.startup_screen_access()
        with patch('macshell.threading.Thread') as thread:
            self.dispatch.call_args.args[0]()
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs['target'], self.api.open_settings)
            self.assertEqual(thread.call_args.kwargs['args'], ('safety', '&mac_access_setup=1'))
            thread.return_value.start.assert_called_once()

    def test_inspection_does_not_record_a_request(self):
        self.api.screen_capture_permission()
        self.assertFalse(mac_screen_access.was_requested())
        self.dispatch.assert_not_called()

    def test_opt_out_and_source_launch_never_request(self):
        self.settings.write_text(json.dumps({'ui': {'mac_screen_request_at_startup': False}}))
        self.api.startup_screen_access()
        self.dispatch.assert_not_called()
        self.settings.write_text('{}')
        with patch.object(sys, 'frozen', False):
            self.api.startup_screen_access()
        self.dispatch.assert_not_called()

    def test_manual_retry_after_denial_opens_settings(self):
        mac_screen_access.mark_requested()
        with patch.object(self.api, 'open_screen_settings') as opened:
            self.api.request_screen_access()
            opened.assert_called_once()
        self.dispatch.assert_not_called()

    def test_damaged_request_marker_is_recoverable(self):
        (self.root / 'screen-access-request.json').write_text('{bad')
        self.api.startup_screen_access()
        self.dispatch.assert_called_once()


class CaptureDiagnosticTests(unittest.TestCase):
    def test_diagnostic_reads_real_settings_interface_and_does_not_save_frame(self):
        from PIL import Image
        from engine_commands import CommandsMixin
        image = io.BytesIO()
        Image.new('RGB', (16, 12)).save(image, 'PNG')
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(Path(folder) / 'settings.json')
            settings.update({'prompter': {'capture_source': 'application', 'capture_application': 'Notes'}})
            with patch('eyes.capture_screen', return_value=image.getvalue()) as capture, patch('tools.desktop_env', return_value={}):
                result = CommandsMixin._command(SimpleNamespace(s=settings, prompter=object()), 'prompter_capture_test', {})
            self.assertEqual(result, {'ok': True, 'width': 16, 'height': 12, 'source': 'application'})
            self.assertEqual(capture.call_args.kwargs['application'], 'Notes')
            self.assertEqual([p.name for p in Path(folder).iterdir()], ['settings.json'])


if __name__ == '__main__':
    unittest.main()
