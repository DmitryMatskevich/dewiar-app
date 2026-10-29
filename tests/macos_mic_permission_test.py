"""Permission states and UI transitions; never asks the OS for real capture access."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "ui")]
import macsys
import app
from macshell import make_api


class MicPermission(unittest.TestCase):
    def test_status_values(self):
        for code, state in enumerate(("not_determined", "restricted", "denied", "authorized")):
            device = SimpleNamespace(authorizationStatusForMediaType_=Mock(return_value=code))
            with self.subTest(state=state), patch("AVFoundation.AVCaptureDevice", device):
                value = macsys.microphone_permission()
                self.assertEqual(value, {"state": state, "granted": code == 3})

    def test_pending_request_and_callback(self):
        auth = {"code": 0}
        device = SimpleNamespace(authorizationStatusForMediaType_=lambda _: auth["code"], requestAccessForMediaType_completionHandler_=Mock())
        with patch("AVFoundation.AVCaptureDevice", device), patch("PyObjCTools.AppHelper.callAfter") as dispatch, patch.object(sys, "frozen", True, create=True):
            api = make_api(app)("fixture")
            self.assertTrue(api.request_microphone_access()["pending"])
            api.request_microphone_access()
            dispatch.assert_called_once()
            dispatch.call_args.args[0]()
            callback = device.requestAccessForMediaType_completionHandler_.call_args.args[1]
            auth["code"] = 3
            callback(True)
            self.assertTrue(api.microphone_permission()["granted"])
            self.assertFalse(api.microphone_permission()["pending"])

    def test_source_cannot_grant_access_to_the_wrong_app(self):
        with patch.object(sys, "frozen", False, create=True), patch("PyObjCTools.AppHelper.callAfter") as dispatch:
            result = make_api(app)("fixture").request_microphone_access()
            self.assertFalse(result["request_supported"])
            dispatch.assert_not_called()

    def test_no_repeated_prompt_for_existing_decision(self):
        for code in (1, 2, 3):
            device = SimpleNamespace(authorizationStatusForMediaType_=lambda _: code)
            with self.subTest(code=code), patch("AVFoundation.AVCaptureDevice", device), patch("PyObjCTools.AppHelper.callAfter") as dispatch, patch.object(sys, "frozen", True, create=True):
                make_api(app)("fixture").request_microphone_access()
                dispatch.assert_not_called()

    def test_request_timeout_does_not_open_another_system_prompt(self):
        clock=[0.]
        with patch('macsys.microphone_permission',return_value={'state':'not_determined','granted':False}),patch('PyObjCTools.AppHelper.callAfter') as dispatch,patch.object(sys,'frozen',True,create=True),patch('macshell.time.monotonic',side_effect=lambda:clock[0]):
            api=make_api(app)('fixture')
            self.assertTrue(api.request_microphone_access()['pending'])
            clock[0]=61
            state=api.request_microphone_access()
            self.assertFalse(state['pending']);self.assertTrue(state['retry_blocked'])
            dispatch.assert_called_once()

    def test_simultaneous_windows_share_one_native_request(self):
        from concurrent.futures import ThreadPoolExecutor
        with patch('macsys.microphone_permission',return_value={'state':'not_determined','granted':False}),patch('PyObjCTools.AppHelper.callAfter') as dispatch,patch.object(sys,'frozen',True,create=True):
            api=make_api(app)('fixture')
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _:api.request_microphone_access(),range(30)))
            dispatch.assert_called_once()

    def test_settings_link_is_fixed(self):
        with patch("macsys.open_target") as opener:
            api = make_api(app)("fixture")
            self.assertTrue(api.open_microphone_settings())
            opener.assert_called_once_with("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")

    def test_ui_permission_states(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            path = Path(tmp) / "permission.html"
            path.write_text('''<div id="mac-mic-access" style="display:none"><p id="mac-mic-status"></p><button id="mac-mic-request">Разрешить доступ</button><button id="mac-mic-refresh">Проверить</button><button id="mac-mic-settings">Настройки</button></div><nav><button data-p="sound"></button></nav><script>
window.permission={state:'not_determined',granted:false,pending:false};window.requests=0;
window.api=()=>({microphone_permission:async()=>permission,request_microphone_access:async()=>{requests++;return permission={state:'not_determined',granted:false,pending:true}},open_microphone_settings:async()=>true});
</script><script src="''' + (ROOT / "ui/studio/mic-permission.js").as_uri() + '''"></script>''')
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(path.as_uri())
            page.locator("#mac-mic-request").wait_for(state="visible")
            page.locator("#mac-mic-request").click()
            self.assertEqual(page.evaluate("requests"), 1)
            self.assertTrue(page.locator("#mac-mic-request").is_disabled())
            for state, text in (("denied", "Доступ запрещён"), ("authorized", "Доступ к микрофону разрешён")):
                page.evaluate("s => permission={state:s,granted:s==='authorized',pending:false}", state)
                page.locator("#mac-mic-refresh").click()
                page.wait_for_function("t => document.getElementById('mac-mic-status').textContent.includes(t)", arg=text)
                self.assertTrue(page.locator("#mac-mic-request").is_hidden())
            page.evaluate("permission={state:'not_determined',granted:false,pending:false,requested:true,retry_blocked:true}")
            page.locator('#mac-mic-refresh').click()
            page.wait_for_function("document.getElementById('mac-mic-status').textContent.includes('Повторно окно не открываю')")
            self.assertTrue(page.locator('#mac-mic-request').is_hidden())
            self.assertEqual(page.evaluate('requests'),1)
            browser.close()


if __name__ == "__main__":
    unittest.main()
