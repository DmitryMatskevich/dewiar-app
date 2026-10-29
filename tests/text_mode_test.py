"""A text-only core must not pretend to record audio or expose a recording timer."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from exam import Core


class TextMode(unittest.TestCase):
    def test_core_rejects_recording_and_unmute(self):
        async def check():
            with tempfile.TemporaryDirectory(prefix="dewiar-text-mode-") as tmp:
                core = Core({"kind": "openai", "model": "fixture", "base_url": "http://127.0.0.1:9/v1"}, tmp)
                try:
                    core.start()
                    await core.connect()
                    snapshot = await core.call("hello")
                    self.assertEqual(snapshot["audio_mode"], "text")
                    for name, args in (("hold_start", {}), ("ptt", {}), ("mute", {"on": False})):
                        with self.subTest(command=name), self.assertRaisesRegex(RuntimeError, "текстовом режиме"):
                            await core.call(name, **args)
                    snapshot = await core.call("hello")
                    self.assertNotEqual(snapshot["state"], "listening")
                    self.assertFalse(any(e.get("type") == "hold" and e.get("on") for e in core.events))
                finally:
                    await core.stop()
        asyncio.run(check())

    def test_hud_opens_text_instead_of_recording(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix="dewiar-text-hud-") as tmp:
            core = Core({"kind": "openai", "model": "fixture", "base_url": "http://127.0.0.1:9/v1"}, tmp)
            try:
                core.start()
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_page(viewport={"width": 340, "height": 500})
                    page.goto((ROOT / "ui/hud.html").as_uri() + f"?mode=card&port={core.port}&token={core.token}")
                    page.wait_for_function("window.core?.connected && document.getElementById('bubble').hidden")
                    self.assertTrue(page.locator("#b-mic").is_disabled())
                    page.evaluate("holdBegin('key')")
                    page.wait_for_timeout(600)
                    self.assertFalse(page.locator("#orb").evaluate("e => e.classList.contains('holding')"))
                    self.assertTrue(page.locator("#askin").is_visible())
                    self.assertTrue(page.locator("#bubble").is_hidden())
                    self.assertTrue(page.locator("#b-voice-inline").evaluate("e => e.classList.contains('on')"))
                    self.assertEqual(page.locator("#note").inner_text(), "")
                    self.assertNotIn("Режим сервера", page.locator("#note").inner_text())
                    page.click("#b-voice-inline")
                    page.wait_for_timeout(250)
                    self.assertTrue(page.locator("#cap").evaluate("e => !e.classList.contains('show')"))
                    browser.close()
            finally:
                asyncio.run(core.stop())


if __name__ == "__main__":
    unittest.main()
