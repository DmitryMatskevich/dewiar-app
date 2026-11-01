"""Native tab import boundaries: no app launch, only allowlisted scripts and web URLs."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import macsys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ui'))
import app as shell

class Sources(unittest.TestCase):
    def test_native_clipboard_bridge(self):
        pasteboard = SimpleNamespace(generalPasteboard=lambda: SimpleNamespace(stringForType_=lambda _: 'https://youtu.be/clipboard'))
        with patch.object(shell.sys, 'platform', 'darwin'), patch.dict(sys.modules, {'AppKit': SimpleNamespace(NSPasteboard=pasteboard, NSPasteboardTypeString='text')}):
            self.assertEqual(shell.Api.clipboard_text(None), 'https://youtu.be/clipboard')

    def test_browser_bridge(self):
        with patch.object(shell.sys, 'platform', 'darwin'), patch('macsys.transcription_browsers', return_value=[{'id':'test'}]), patch('macsys.transcription_browser_url', return_value={'url':'https://youtu.be/test'}) as read:
            self.assertEqual(shell.Api.transcription_browsers(None), [{'id':'test'}])
            self.assertEqual(shell.Api.transcription_browser_url(None, 'test'), {'url':'https://youtu.be/test'})
            read.assert_called_once_with('test')
        with patch.object(shell.sys, 'platform', 'linux'):
            self.assertEqual(shell.Api.transcription_browsers(None), [])
            self.assertIn('error', shell.Api.transcription_browser_url(None, 'test'))

    def test_running_browsers_only(self):
        apps = [SimpleNamespace(bundleIdentifier=lambda key=key: key) for key in ('com.apple.Safari', 'org.mozilla.firefox')]
        workspace = SimpleNamespace(sharedWorkspace=lambda: SimpleNamespace(runningApplications=lambda: apps))
        with patch.dict(sys.modules, {'AppKit': SimpleNamespace(NSWorkspace=workspace)}):
            self.assertEqual(macsys.transcription_browsers(), [{'id':'com.apple.Safari','name':'Safari'}])

    def test_unknown_browser_never_executes(self):
        with patch('macsys.subprocess.run') as run:
            self.assertIn('error', macsys.transcription_browser_url('evil"\n do shell script "bad'))
            run.assert_not_called()

    def test_browser_dialects(self):
        for key, tab in [('com.apple.Safari','current tab'), ('ru.yandex.desktop.yandex-browser','active tab')]:
            with self.subTest(key=key), patch('macsys.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='https://youtu.be/video\n')) as run:
                self.assertEqual(macsys.transcription_browser_url(key)['url'], 'https://youtu.be/video')
                args = run.call_args.args[0]
                self.assertEqual(args[:2], ['/usr/bin/osascript','-e'])
                self.assertIn(tab, args[2])
                self.assertIn('if not running', args[2])
                self.assertNotIn('activate', args[2])
                self.assertNotIn('shell', run.call_args.kwargs)

    def test_bad_urls_and_closed_windows(self):
        for url in ('', 'about:blank', 'file:///private/file', 'javascript:alert(1)', 'https://', 'https://[', 'https://a.test/\nhttps://b.test/'):
            with self.subTest(url=url), patch('macsys.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=url)):
                self.assertIn('error', macsys.transcription_browser_url('com.apple.Safari'))

    def test_denied_and_timeout(self):
        with patch('macsys.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            self.assertIn('Автоматизация', macsys.transcription_browser_url('com.apple.Safari')['error'])
        with patch('macsys.subprocess.run', side_effect=subprocess.TimeoutExpired('osascript',20)):
            self.assertIn('не ответил', macsys.transcription_browser_url('com.apple.Safari')['error'])

if __name__ == '__main__':
    unittest.main()
