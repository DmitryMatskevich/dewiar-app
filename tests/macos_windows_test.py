"""Cocoa pasteboard on a private board, and bridge contracts without GTK or personal data."""
import base64
import io
import sys
import unittest
import plistlib
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "ui")]
import macsys
import app
from macshell import make_api, core_authenticated, free_core_port


@unittest.skipUnless(sys.platform == "darwin", "Cocoa only")
class MacClipboard(unittest.TestCase):
    def setUp(self):
        from AppKit import NSPasteboard
        self.board = NSPasteboard.pasteboardWithUniqueName()

    def tearDown(self):
        self.board.releaseGlobally()

    def test_png_pixels(self):
        from Foundation import NSData
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (7, 9), (11, 22, 33)).save(buf, "PNG")
        raw = buf.getvalue()
        self.board.setData_forType_(NSData.dataWithBytes_length_(raw, len(raw)), "public.png")
        result = Image.open(io.BytesIO(base64.b64decode(macsys.clipboard_image(self.board).split(",", 1)[1])))
        self.assertEqual(result.size, (7, 9))
        self.assertEqual(result.getpixel((1, 1)), (11, 22, 33))

    def test_tiff_conversion(self):
        from Foundation import NSData
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (5, 6), "red").save(buf, "TIFF")
        raw = buf.getvalue()
        self.board.setData_forType_(NSData.dataWithBytes_length_(raw, len(raw)), "public.tiff")
        result = Image.open(io.BytesIO(base64.b64decode(macsys.clipboard_image(self.board).split(",", 1)[1])))
        self.assertEqual(result.format, "PNG")
        self.assertEqual(result.size, (5, 6))

    def test_text_is_not_image(self):
        self.board.setString_forType_("file:///private/example.png", "public.utf8-plain-text")
        self.assertEqual(macsys.clipboard_image(self.board), "")

    def test_secret_expires(self):
        with patch("threading.Timer") as timer:
            self.assertTrue(macsys.clip_secret("fixture-secret", board=self.board))
            timer.call_args.args[1]()
            self.assertIsNone(self.board.stringForType_("public.utf8-plain-text"))
            self.assertTrue(timer.return_value.daemon)

    def test_later_copy_is_preserved(self):
        with patch("threading.Timer") as timer:
            macsys.clip_secret("fixture-secret", board=self.board)
            self.board.clearContents()
            self.board.setString_forType_("new content", "public.utf8-plain-text")
            timer.call_args.args[1]()
            self.assertEqual(self.board.stringForType_("public.utf8-plain-text"), "new content")

    def test_same_text_recopied_is_preserved(self):
        with patch("threading.Timer") as timer:
            macsys.clip_secret("fixture-secret", board=self.board)
            self.board.clearContents()
            self.board.setString_forType_("fixture-secret", "public.utf8-plain-text")
            timer.call_args.args[1]()
            self.assertEqual(self.board.stringForType_("public.utf8-plain-text"), "fixture-secret")


class MacBridge(unittest.TestCase):
    def setUp(self):
        self.api = make_api(app)("fixture-token")

    def test_capture_status_has_no_linux_command(self):
        state = self.api.capture_status()
        self.assertFalse(state["works"])
        self.assertFalse(state["command"])
        self.assertIn("Mac", state["how"])

    def test_unrelated_http_server_is_not_a_core(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_error(404)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.assertFalse(core_authenticated(server.server_port, "fixture-token"))
        finally:
            server.shutdown()
            server.server_close()

    def test_free_core_port_is_not_occupied(self):
        import socket
        port = free_core_port()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))

    def test_resize_accepts_frontend_final_argument(self):
        from unittest.mock import Mock
        self.api._hud = Mock()
        self.api.resize_card(340, 500, True)
        self.api._hud.resize.assert_called_once_with(340, 500)

    def test_from_tray_recreates_closed_hud(self):
        from unittest.mock import Mock
        class Event:
            def __iadd__(self, callback):
                self.callback = callback
                return self
        window = Mock()
        window.events.closed = Event()
        self.api._hud = None
        with patch("webview.create_window", return_value=window) as create:
            self.api.from_tray()
        create.assert_called_once()
        window.show.assert_called()
        window.restore.assert_called_once_with()
        self.assertIs(self.api._hud, window)

    def test_external_urls_keep_telegram_and_reject_file(self):
        with patch("macsys.open_target") as opener:
            # make_api binds the opener at class creation time.
            api = make_api(app)("fixture")
            self.assertTrue(api.open_url("tg://resolve?domain=example"))
            opener.assert_called_once()
            with self.assertRaises(ValueError):
                api.open_url("file:///bin/sh")

    def test_core_opens_url_with_mac_launcher(self):
        import tools
        with patch.object(tools, "MAC", True), patch.object(tools, "DRY", None), patch.object(tools, "EXAM_LAUNCH", None), patch("macsys.open_target") as opener:
            tools.t_open_url({"url": "https://example.invalid/fixture"}, {})
            opener.assert_called_once_with("https://example.invalid/fixture")

    def test_app_inventory_and_launch(self):
        import tools
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / "Utilities/Fixture.app"
            (bundle / "Contents").mkdir(parents=True)
            (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Fixture", "CFBundleIdentifier": "test.fixture"}))
            apps = macsys.applications([folder])
            self.assertEqual(apps[0]["path"], str(bundle))
            with patch.object(tools, "MAC", True), patch.object(tools, "desktop_apps", return_value=apps), patch.object(tools, "launch") as launch:
                tools.t_open_app({"name": "Fixture"}, {})
                launch.assert_called_once_with(["xdg-open", str(bundle)])

    def test_screen_permission_denial_never_captures(self):
        with patch("Quartz.CGPreflightScreenCaptureAccess", return_value=False), patch("PIL.ImageGrab.grab") as grab:
            with self.assertRaisesRegex(PermissionError, "Запись экрана"):
                macsys.screen_capture()
            grab.assert_not_called()

    def test_screen_png_and_core_dispatch(self):
        import eyes
        from PIL import Image
        with patch("Quartz.CGPreflightScreenCaptureAccess", return_value=True), patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (8, 6), "blue")):
            raw = macsys.screen_capture()
            self.assertEqual(Image.open(io.BytesIO(raw)).size, (8, 6))
        with patch.object(eyes, "MAC", True), patch("macsys.screen_capture", return_value=raw) as capture:
            self.assertEqual(eyes.capture_screen({}), raw)
            capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
