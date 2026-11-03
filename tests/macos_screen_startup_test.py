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
from unittest.mock import Mock, patch

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


class FilesStartupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name); self.settings = self.root / 'settings.json'
        self.settings.write_text('{}'); self.build = 'files-A'
        for target, kwargs in [
            ('mac_screen_access.STATE_DIR', {'new':self.root}),
            ('mac_screen_access.build_id', {'side_effect':lambda:self.build}),
            ('app.SETTINGS_PATH', {'new':self.settings}),
            ('sys.frozen', {'new':True, 'create':True}),
            ('macshell.threading.Thread', {}),
            ('macshell.time.sleep', {}),
            ('macshell._core_request', {'side_effect':lambda token, cmd, args, **kw:
                {'state':'authorized', 'readable':True, 'path':'/fixture/'+args['target']}}),
        ]:
            p = patch(target, **kwargs); mock = p.start(); self.addCleanup(p.stop)
            if target.endswith('Thread'): self.thread = mock
            if target.endswith('_core_request'): self.core = mock
        self.api = make_api(app)('fixture')
        self.api.screen_capture_permission = Mock(return_value={'pending':False})
        self.api.open_settings = Mock(); self.api.open_files_settings = Mock()

    def run_request(self):
        self.thread.call_args.kwargs['target']()

    def test_waits_for_screen_and_runs_once_per_build(self):
        def status():
            self.core.assert_not_called()
            return {'pending':True}
        self.api.screen_capture_permission.side_effect = [status(), {'pending':False}]
        self.api.startup_files_access(); self.api.startup_files_access()
        self.thread.assert_called_once(); self.core.assert_not_called()
        self.run_request()
        self.assertEqual([c.args[2]['target'] for c in self.core.call_args_list], ['workspace', 'documents'])
        self.assertTrue(mac_screen_access.was_requested('files'))
        self.assertFalse(mac_screen_access.was_requested())
        make_api(app)('fixture').startup_files_access()
        self.thread.assert_called_once()
        self.build = 'files-B'
        make_api(app)('fixture').startup_files_access()
        self.assertEqual(self.thread.call_count, 2)

    def test_denial_opens_files_settings_and_keeps_results(self):
        self.core.side_effect = lambda token, cmd, args, **kw: {'state':'denied', 'readable':False, 'path':'/fixture/'+args['target']}
        self.api.startup_files_access(); self.run_request()
        self.api.open_files_settings.assert_called_once()
        self.api.open_settings.assert_called_once_with('safety', '&mac_access_setup=1')
        self.assertEqual(self.api._files_access['documents']['state'], 'denied')
        self.assertEqual(self.api._files_pending, '')
        self.assertTrue(mac_screen_access.was_requested('files'))

    def test_unavailable_core_is_retryable_and_never_claims_access(self):
        self.core.side_effect = TimeoutError('core unavailable')
        self.api.startup_files_access(); self.run_request()
        self.assertFalse(mac_screen_access.was_requested('files'))
        self.assertFalse(getattr(self.api, '_files_access', {}))
        self.assertFalse(self.api._files_startup_running)
        self.assertEqual(self.api._files_pending, '')
        self.api.open_settings.assert_called_once()

    def test_opt_out_source_and_explicit_request_are_respected(self):
        self.settings.write_text('{"ui":{"mac_files_request_at_startup":false}}')
        self.api.startup_files_access(); self.thread.assert_not_called()
        self.settings.write_text('{}')
        with patch.object(sys, 'frozen', False): self.api.startup_files_access()
        self.thread.assert_not_called()
        self.api._files_pending = 'selected'
        self.api.startup_files_access(); self.run_request()
        self.core.assert_not_called()
        self.assertEqual(self.api._files_pending, 'selected')
        self.assertFalse(mac_screen_access.was_requested('files'))


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
