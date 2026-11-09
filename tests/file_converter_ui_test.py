"""HUD conversion/OCR actions and chunked large-file upload in an isolated test UI."""
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="dewiar-file-converter-ui-") as folder, sync_playwright() as pw:
    ui = Path(folder) / "ui"
    shutil.copytree(ROOT / "ui", ui)
    (ui / "assets/dewiar.js").write_text('''
      window.calls=[];window.api=()=>({});window.core={connected:true,_on:{},snapshot:null,
        on(k,f){(this._on[k]??=[]).push(f);return this},
        async call(cmd,args){calls.push([cmd,args]);
          if(cmd==='file_convert_begin')return {upload_id:'upload-test',chunk_bytes:2097152};
          if(cmd==='file_convert_chunk')return {received:args.b64.length,size:0};
          if(cmd==='file_convert_finish'||cmd==='file_convert')return {id:'conversion-test'};
          if(cmd==='text_extract')return {id:'ocr-test'};
          if(cmd==='task_read_result')return args.id==='ocr-test'?{text:'Распознанный текст 42',truncated:false}:{text:'# Результат\\n\\n| Код | Значение |\\n| --- | --- |\\n| 001 | Тест |',truncated:false};
          if(cmd==='task_write_result')return {saved:true,path:args.path};
          if(cmd==='task_preview_result')return {mime:'image/png',data:'iVBORw0KGgo='};
          if(cmd==='task_open_result')return {opened:true};
          if(cmd==='task_export')return {dir:'/tmp',name:'result.pdf'};
          return {};
        },async sendLater(cmd,args){calls.push([cmd,args]);return {}}};
    ''', encoding="utf-8")
    hud = ui / "hud.html"
    markup = hud.read_text(encoding="utf-8")
    marker = '<div id="test-banner" style="position:fixed;z-index:99999;top:0;left:0;padding:4px 10px;background:#a00;color:#fff;font:bold 13px sans-serif">ТЕСТ — отдельные данные</div><script>document.title="ТЕСТ — отдельные данные"</script>'
    hud.write_text(markup.replace("</body>", marker + "</body>"), encoding="utf-8")
    browser = getattr(pw, os.environ.get("DEWIAR_TEST_BROWSER", "chromium")).launch()
    page = browser.new_page(viewport={"width": 620, "height": 780})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(hud.as_uri())
    page.wait_for_function("typeof core !== 'undefined' && typeof addFiles === 'function'")
    assert page.title() == "ТЕСТ — отдельные данные"
    assert page.locator("#test-banner").inner_text() == "ТЕСТ — отдельные данные"
    assert page.evaluate("document.querySelector('#b-tasks').compareDocumentPosition(document.querySelector('#b-notes')) & Node.DOCUMENT_POSITION_FOLLOWING")
    assert page.evaluate("getComputedStyle(document.querySelector('#b-tasks')).color===getComputedStyle(document.querySelector('#b-notes')).color")
    page.evaluate("openAsk();applyLook({skin:'neusky'})")
    page.locator("#a-file").set_input_files({"name": "table.csv", "mimeType": "text/csv", "buffer": "a,b\n1,2\n".encode()})
    page.wait_for_selector(".att-convert")
    button = page.locator(".att-convert")
    assert button.get_attribute("aria-label") == "Выбрать формат преобразования для этого файла"
    assert "результат появится сверху" in button.get_attribute("title")
    button.click()
    bounds = page.locator('.att-format-picker').bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['y'] >= 0
    assert bounds['x'] + bounds['width'] <= 620 and bounds['y'] + bounds['height'] <= 780
    page.get_by_role("button", name="В XLSX").click()
    page.wait_for_function("calls.some(x=>x[0]==='file_convert')")
    call = page.evaluate("calls.find(x=>x[0]==='file_convert')")
    assert call[1]["target"] == "xlsx" and call[1]["files"][0]["name"] == "table.csv"

    # The OCR icon creates a native text-extraction task from original image bytes.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
    page.locator("#a-file").set_input_files({"name":"scan.png","mimeType":"image/png","buffer":png})
    page.wait_for_selector(".image-extract-task")
    task_icon = page.locator(".image-extract-task")
    assert task_icon.get_attribute("aria-label").startswith("Распознать текст")
    assert task_icon.inner_text() == ""
    assert "локальным OCR" in task_icon.get_attribute("title")
    assert page.evaluate("getComputedStyle(document.querySelector('.image-extract-task')).borderTopWidth==='0px'")
    task_icon.click()
    page.wait_for_function("calls.some(x=>x[0]==='text_extract')")
    extraction = page.evaluate("calls.find(x=>x[0]==='text_extract')")
    assert extraction[1]["files"][0]["name"] == "scan.png"
    page.evaluate("core._on.task.forEach(fn=>fn({task:{id:'ocr-test',workflow:'text-extraction',status:'done',title:'Извлечь текст из scan.png',outputs:[{path:'outputs/scan.txt',title:'scan.txt',source:'scan.png'}]}}))")
    page.wait_for_function("document.querySelector('#bot').textContent.includes('Распознанный текст 42')")
    assert page.locator("#bot").get_by_role("button", name="Скопировать показанный текст в буфер обмена").count() == 1
    page.evaluate("core._on.task.forEach(fn=>fn({task:{id:'conv-done',workflow:'file-conversion',status:'done',title:'Преобразовать table.csv в PDF',outputs:[{path:'outputs/table.pdf',title:'table.pdf',source:'table.csv'}]}}))")
    assert page.locator("#bot").get_by_text("table.csv → table.pdf").count() == 1
    page.locator("#bot").get_by_role("button", name="Открыть результат table.pdf").click()
    page.wait_for_function("calls.some(x=>x[0]==='task_open_result')")
    page.evaluate("core._on.task.forEach(fn=>fn({task:{id:'conv-md',workflow:'file-conversion',status:'done',title:'Преобразовать report.docx в MD',outputs:[{path:'outputs/report.md',title:'report.md',source:'report.docx'},{path:'outputs/preview.png',title:'preview.png',source:'report.docx'}]}}))")
    page.wait_for_selector(".native-operation-editor", state="detached")
    results_link = page.locator("#bot").get_by_role("button", name="Открыть задачу «Преобразовать report.docx в MD» и вкладку с файлами результата")
    assert results_link.count() == 2
    page.locator("#bot").get_by_role("button", name="Редактировать report.md").click()
    editor = page.locator(".native-operation-editor")
    editor.fill("# Исправленный результат\n")
    page.locator("#bot").get_by_role("button", name="Сохранить изменения").click()
    page.wait_for_function("calls.some(x=>x[0]==='task_write_result'&&x[1].text==='# Исправленный результат\\n')")
    assert page.locator("#bot").get_by_role("img", name="preview.png").count() == 1

    # >10 MiB uses bounded chunks and becomes a task without placing a huge base64
    # body into one bridge request.
    page.locator("#a-file").set_input_files({"name": "large.txt", "mimeType": "text/plain", "buffer": b"x" * (10 * 1024 * 1024 + 1)})
    page.wait_for_function("document.querySelectorAll('.att-convert').length===3")
    page.locator(".att-convert").nth(2).click()
    page.get_by_role("button", name="В PDF", exact=True).click()
    page.wait_for_function("calls.some(x=>x[0]==='file_convert_finish')", timeout=30000)
    names = page.evaluate("calls.map(x=>x[0])")
    assert "file_convert_begin" in names and "file_convert_chunk" in names and "file_convert_finish" in names
    chunk_calls = page.evaluate("calls.filter(x=>x[0]==='file_convert_chunk').length")
    assert chunk_calls >= 5
    assert not errors, errors
    browser.close()
print("ALL OK: test banner, conversion picker, OCR icon, top text/file results, borderless actions, and bounded upload over 10 MiB")
