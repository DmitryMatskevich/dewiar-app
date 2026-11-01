"""Click the actual HUD button through the real command → note/OCR pipeline.

Only desktop pixels and OCR are fixtures; workspace files, image preparation,
core dispatch, HUD handlers and unread-note acknowledgement are real.
"""
import base64
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from engine_commands import CommandsMixin
from workspace import Workspace
from settings import Settings

with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
    root = Path(folder)
    settings = Settings(root / "settings.json")
    settings.update({"workspace": {"path": str(root / "workspace")}})
    ws = Workspace(settings)
    image = io.BytesIO()
    Image.new("RGB", (700, 400), "white").save(image, "PNG")
    png = image.getvalue()
    events = []
    engine = SimpleNamespace(workspace=ws, toolbox=SimpleNamespace(policy=Mock(return_value=(True, False))),
                             note=Mock(), emit=lambda event_type, **data: events.append([event_type, data]))
    allowed = True
    def screenshot():
        events.clear()
        engine.toolbox.policy.return_value = (allowed, False)
        with patch("eyes.capture_screen", return_value=png) as capture, patch("ocr.recognize_result", return_value={
                "status": "done", "engine": "fixture", "text": "Решение 42\nProject Alpha 123", "lines": []}):
            try:
                url = CommandsMixin.command(engine, "screenshot_attach", {})
            except ValueError as error:
                if allowed:
                    raise
                assert capture.call_count == 0
                return {"error": str(error)}
            capture.assert_called_once()
        return {"url": url, "events": list(events)}

    ui = root / "ui"
    shutil.copytree(ROOT / "ui", ui)
    (ui / "assets/dewiar.js").write_text('''
      window.opens=[];window.sent=[];
      window.api=()=>({async open_settings(...args){opens.push(args);return true;}});
      window.core={_on:{},on(k,f){(this._on[k]??=[]).push(f);return this},
        async call(cmd,args){if(cmd!=='screenshot_attach'){sent.push([cmd,args]);return {}};
          const result=await nativeScreenshot();if(result.error)throw Error(result.error);
          for(const [kind,data] of result.events)fire(kind,data);return result.url;}};
      window.fire=(type,data)=>(core._on[type]||[]).forEach(fn=>fn(data));
    ''')
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page(viewport={"width": 420, "height": 850})
    page.expose_function("nativeScreenshot", screenshot)
    page.goto((ui / "hud.html").as_uri())
    page.evaluate("openAsk();applyLook({skin:'neusky'});showCap('','')")
    page.locator("#a-shot").click()
    try:
        page.wait_for_function("atts.length===1 && unreadNotes.size===1", timeout=5000)
    except Exception:
        print("Screenshot failure:", page.locator("#bot").inner_text(), "events:", events)
        raise
    notes = list(ws.root.glob("Заметки/Снимки экрана/*.md"))
    assert len(notes) == 1
    doc = notes[0].read_text()
    assert "Решение 42\nProject Alpha 123" in doc
    assert "Экран.png" in doc and "Распознанный текст.txt" in doc
    saved = [data for kind, data in events if kind == "note_saved"]
    assert len(saved) == 2 and saved[0]["path"] == saved[1]["path"]
    assert next(ws.root.glob("Заметки/**/Экран.png")).read_bytes() == png
    data_url = page.evaluate("atts[0].url")
    assert Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).size == (700, 400)
    button = page.locator("#b-notes")
    assert button.evaluate("e=>e.classList.contains('has-new-notes')")
    button.click(force=True)
    assert page.evaluate("opens.at(-1)[0]") == "notes"
    assert page.evaluate("opens.at(-1)[1]").startswith("&note=")
    page.evaluate("path=>fire('notes_seen',{path})", saved[0]["path"])
    assert page.locator("#b-notes-dot").is_hidden()
    # A disabled screen policy must neither take a new screenshot nor create a note.
    allowed = False
    page.locator("#a-shot").click(force=True)
    page.wait_for_function("document.querySelector('#bot').textContent.includes('Снимок экрана отключён')")
    assert len(list(ws.root.glob("Заметки/Снимки экрана/*.md"))) == 1
    assert page.evaluate("atts.length") == 1
    assert not page.evaluate("sent.some(([cmd])=>cmd==='say')")
    browser.close()
print("ALL OK: real screenshot button saves one OCR note, attaches same image, lights Notes, opens note, respects disabled policy")
