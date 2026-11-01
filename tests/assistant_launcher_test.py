"""Window lifecycle and owned-core launch, without opening Cocoa windows or real cores."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "ui")]
import macshell


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


class Window:
    def __init__(self):
        self.events = SimpleNamespace(**{name: Event() for name in ("closed", "shown", "restored", "minimized")})
        self.show = Mock(side_effect=self.events.shown.fire)
        self.hide = Mock()
        self.restore = Mock()


class Base:
    def __init__(self, token):
        self._hud = None
        self.in_tray = False
        self.wins = {}

    def _url(self, *args):
        return "fixture://hud"


class AssistantLauncher(unittest.TestCase):
    def setUp(self):
        self.api = macshell.make_api(SimpleNamespace(Api=Base))("fixture-token")

    def test_installed_webview_supports_lifecycle_events(self):
        import webview
        window = webview.create_window("fixture-not-shown", html="<p>fixture</p>")
        try:
            for name in ("closed", "shown", "restored", "minimized"):
                self.assertTrue(hasattr(window.events, name), name)
        finally:
            webview.windows.remove(window)  # no GUI loop is started by this test

    def test_closed_window_is_recreated_and_old_events_do_not_hide_it(self):
        first, second = Window(), Window()
        self.api._wake_core = Mock()
        with patch("webview.create_window", side_effect=[first, second]) as create:
            self.assertFalse(self.api.tray_state()["loaded"])
            self.assertTrue(self.api.wake_agent()["visible"])
            self.api.hide()
            self.assertEqual(self.api.tray_state()["visible"], False)
            self.assertTrue(self.api.tray_state()["loaded"])
            self.api.wake_agent()
            self.assertEqual(create.call_count, 1)
            first.events.closed.fire()
            self.assertFalse(self.api.tray_state()["loaded"])
            self.assertTrue(self.api.wake_agent()["visible"])
            self.assertEqual(create.call_count, 2)
            first.events.closed.fire()
            first.events.minimized.fire()
            self.assertIs(self.api._hud, second)
            self.assertTrue(self.api.tray_state()["visible"])

    def test_core_failure_does_not_report_open_window(self):
        self.api._wake_core = Mock(side_effect=RuntimeError("fixture launch failure"))
        with patch("webview.create_window") as create:
            with self.assertRaisesRegex(RuntimeError, "fixture launch failure"):
                self.api.wake_agent()
            create.assert_not_called()
            self.assertFalse(self.api.tray_state()["loaded"])

    def test_native_hide_and_show_are_reflected_in_status(self):
        window = Window()
        with patch("webview.create_window", return_value=window):
            self.api.wake_agent()
            window.events.minimized.fire()
            self.assertFalse(self.api.tray_state()["visible"])
            window.events.restored.fire()
            self.assertTrue(self.api.tray_state()["visible"])

    def test_launch_success_returns_owned_process_and_log(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(macshell, "STATE_DIR", Path(folder)), \
                patch.object(macshell, "core_authenticated", return_value=True), \
                patch.object(macshell.subprocess, "Popen") as spawn:
            proc, stream = macshell.start_owned_core(SimpleNamespace(ROOT=ROOT), "fixture", True, 54321)
            try:
                self.assertIs(proc, spawn.return_value)
                self.assertIn("--server", spawn.call_args.args[0])
                self.assertFalse(stream.closed)
                proc.terminate.assert_not_called()
            finally:
                stream.close()

    def test_failed_launch_cleans_process_and_log(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(macshell, "STATE_DIR", Path(folder)), \
                patch.object(macshell, "core_authenticated", side_effect=RuntimeError("fixture failure")), \
                patch.object(macshell.subprocess, "Popen") as spawn:
            spawn.return_value.poll.return_value = None
            spawn.return_value.wait.side_effect = [subprocess.TimeoutExpired("fixture", 3), 0]
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                macshell.start_owned_core(SimpleNamespace(ROOT=ROOT), "fixture", True, 54321)
            spawn.return_value.terminate.assert_called_once()
            spawn.return_value.kill.assert_called_once()
            self.assertTrue(spawn.call_args.kwargs["stdout"].closed)


if __name__ == "__main__":
    unittest.main()
