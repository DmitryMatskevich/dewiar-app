"""Browser-level smoke test for visual HTML editing, AI proposals, save, and two windows."""
import json
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
HTML = ('<!doctype html><html><head><meta charset="utf-8"><base href="https://lanmaster.ru/">'
        '<title>LANMASTER</title><style>.hero{color:#1c3456;padding:18px}</style></head><body>'
        '<section class="hero"><h1>Каталог LANMASTER</h1><p>Проверка редактора</p>'
        '<img src="https://lanmaster.ru/favicon.ico" alt="LANMASTER"></section></body></html>')
MOCK_BRIDGE = (
    'window.core={snapshot:null,on:(name,fn)=>{if(name==="hello")setTimeout(fn,20)},call:async(cmd,args)=>{'
    'if(cmd==="task_html_editor_load")return {html:' + json.dumps(HTML, ensure_ascii=False) + ',path:args.path,title:"LANMASTER.html",revision:"r1"};'
    'if(cmd==="task_html_editor_save"){window.__savedHTML=args.html;return {path:"outputs/edited/LANMASTER.html",revision:"r2"};}'
    'if(cmd==="html_editor_suggest"){const node=(args.nodes||[]).find(row=>row.tag==="h1");return {reply:"Изменю заголовок.",operations:node?[{op:"set_text",target:node.id,text:"LANMASTER — редактор работает"}]:[]};}'
    'throw Error(cmd)}};window.api=()=>({list_html_editors:async()=>Object.entries(window.__allEditors).map(([key,title])=>({key,title})),'
    'html_editor_transfer:async(key,payload)=>{window.__transferPayload=payload;return true},html_editor_set_title:()=>true,save_file:async()=>null});'
)


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(UI), **kwargs)
    def log_message(self, *args): pass


class HtmlEditorBrowserTest(unittest.TestCase):
    def test_two_windows_ai_visual_canvas_save_and_element_transfer(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        browser = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1400, "height": 900})
                context.route("**/assets/dewiar.js", lambda route: route.fulfill(status=200, content_type="application/javascript", body=MOCK_BRIDGE))
                pages = {}
                for key in ("one", "two"):
                    page = context.new_page(); pages[key] = page
                    page.add_init_script('window.__allEditors={one:"Окно 1",two:"Окно 2"};')
                    page.goto(f"http://127.0.0.1:{server.server_port}/html-editor.html?task=test&path=outputs%2Fpage.html&doc={key}", wait_until="domcontentloaded")
                    page.wait_for_function("document.querySelector('#status').textContent.includes('загружен')")
                    page.wait_for_selector("iframe.gjs-frame")

                source, target = pages["one"], pages["two"]
                source.wait_for_function("document.querySelector('#target-window').options.length >= 2")
                canvas = source.frame_locator("iframe.gjs-frame")
                canvas.locator("h1").click()
                self.assertTrue(canvas.locator("h1").evaluate("el => el.draggable"))
                self.assertEqual(canvas.locator("h1").inner_text(), "Каталог LANMASTER")
                source.locator("#ai-request").fill("Поменяй заголовок")
                source.locator("#ai-ask").click()
                source.get_by_text("Применить", exact=True).click()
                source.locator("#save").click()
                source.wait_for_function("window.__savedHTML")
                self.assertIn("LANMASTER — редактор работает", source.evaluate("window.__savedHTML"))

                canvas.locator("h1").click()
                source.select_option("#target-window", "two")
                source.locator("#send-block").click()
                source.wait_for_function("window.__transferPayload")
                target.evaluate("payload => window.htmlEditorReceiveTransfer(payload)", source.evaluate("window.__transferPayload"))
                self.assertIn("Блок вставлен", target.locator("#status").inner_text())
                self.assertGreaterEqual(target.frame_locator("iframe.gjs-frame").locator("h1").count(), 2)
                browser.close(); browser = None
        finally:
            if browser: browser.close()
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__": unittest.main(verbosity=2)
