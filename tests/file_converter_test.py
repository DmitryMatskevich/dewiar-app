"""Focused, isolated integration checks for local file conversion and its native task runner."""
import base64
import csv
import io
import math
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import file_converter as FC
from engine_commands import CommandsMixin
from tasks import TaskManager


class Workspace:
    def __init__(self, root): self.root = Path(root)
    def ensure(self): self.root.mkdir(parents=True, exist_ok=True); return self.root


class Host:
    def __init__(self, root):
        self.workspace = Workspace(root)
        self.stopping = threading.Event(); self.stopping.set()
        self.events, self.announcements = [], []
    def persona_name(self): return "Тестовый помощник"
    def emit(self, *args, **kwargs): self.events.append((args, kwargs))
    def announce(self, text): self.announcements.append(text)
    def llm_profile(self): raise AssertionError("file-conversion workflow must not request an LLM profile")


class UploadTasks:
    def create_file_conversion_from_paths(self, files, target, goal=""):
        self.seen = {"files": files, "target": target, "goal": goal, "bytes": Path(files[0]["path"]).read_bytes()}
        return type("CreatedTask", (), {"public": lambda _self: {"id": "chunk-task", "status": "running"}})()


class UploadHarness(CommandsMixin):
    def __init__(self, root):
        self.workspace = Workspace(root)
        self.tasks = UploadTasks()

class ExtractionTasks:
    def create_text_extraction(self, files, goal=''):
        self.seen = {'files':files,'goal':goal}
        return type('CreatedTask', (), {'id':'ocr-task','public':lambda _self:{'id':'ocr-task','status':'running'}})()

class ExtractionCommandHarness(CommandsMixin):
    def __init__(self): self.tasks=ExtractionTasks(); self.quick=[]
    def _say_quick(self, text, source=''): self.quick.append((text,source))

class WebPageTasks:
    def create_webpage_conversion(self, url, target, goal=''):
        self.seen = (url, target, goal)
        return type('WebTask', (), {'id':'page-task','public': lambda _self: {'id':'page-task','status':'running'}})()

class WebPageCommandHarness(CommandsMixin):
    def __init__(self): self.tasks=WebPageTasks(); self.quick=[]
    def _say_quick(self, text, source=''): self.quick.append((text,source))

class ResultRecord:
    def __init__(self, folder, outputs):
        self.dir = Path(folder)
        self.data = {'workflow':'file-conversion','outputs':outputs}
class ResultTasks:
    def __init__(self, record): self.record=record
    def get(self, task_id):
        if task_id != 'result-task': raise ValueError('Задача не найдена')
        return self.record
class ResultCommandHarness(CommandsMixin):
    def __init__(self, record): self.tasks=ResultTasks(record)


class FileConverterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dewiar-file-convert-test-")
        self.root = Path(self.temp.name)
        self.out = self.root / "out"
        self.out.mkdir()

    def tearDown(self): self.temp.cleanup()

    def convert(self, name, target): return FC.convert(self.root / name, target, self.out)

    def test_command_aliases(self):
        self.assertEqual(FC.requested_target("Лира, преобразовать в PDF"), "pdf")
        self.assertEqual(FC.requested_target("Лира, конвертировать Excel в CSV"), "csv")
        self.assertEqual(FC.requested_target("Лира, преобразуй в Word"), "docx")
        self.assertTrue(FC.is_conversion_command("Лира, преобразовать"))
        self.assertTrue(FC.is_text_extraction_command("Лира, извлечь текст из картинки"))
        self.assertTrue(FC.is_text_extraction_command("распознай текст"))
        self.assertTrue(FC.is_text_extraction_command("Лира, получить текст"))
        self.assertIsNone(FC.requested_target("Расскажи про PDF"))

    def test_local_ocr_background_task_saves_text_without_model(self):
        import base64
        from PIL import Image
        from unittest.mock import patch
        image = Image.new('RGB', (60, 30), 'white'); data = io.BytesIO(); image.save(data, 'PNG')
        encoded = base64.b64encode(data.getvalue()).decode()
        host = Host(self.root / 'ocr-workspace')
        manager = TaskManager(host)
        with patch('ocr.recognize_result', return_value={'status':'done','text':'Распознанный текст 42','engine':'fixture'}):
            task = manager.create_text_extraction([{'name':'скан.png','b64':encoded}], 'Лира, извлечь текст')
            deadline = time.time() + 10
            while task.thread and task.thread.is_alive() and time.time() < deadline: time.sleep(.02)
        self.assertEqual(task.status, 'done', task.error)
        output = task.dir / task.outputs[0]['path']
        self.assertEqual(output.read_text(encoding='utf-8'), 'Распознанный текст 42')
        self.assertEqual(task.data['workflow'], 'text-extraction')
        self.assertIn('модели не используются', (task.dir / 'журнал.md').read_text(encoding='utf-8').lower())

    def test_pdf_text_layer_extraction_uses_local_converter(self):
        import base64
        from unittest.mock import patch
        manager = TaskManager(Host(self.root / 'pdf-extraction-workspace'))
        def fake_convert(src, target, output_dir, **kwargs):
            output = Path(output_dir) / 'Отчёт.txt'
            output.write_text('Текстовый слой PDF 2026', encoding='utf-8')
            return {'path':output, 'backend':'fixture'}
        with patch('file_converter.convert', side_effect=fake_convert):
            task = manager.create_text_extraction([{'name':'report.pdf','b64':base64.b64encode(b'%PDF fixture').decode()}])
            deadline = time.time() + 10
            while task.thread and task.thread.is_alive() and time.time() < deadline: time.sleep(.02)
        self.assertEqual(task.status, 'done', task.error)
        self.assertEqual((task.dir / task.outputs[0]['path']).read_text(encoding='utf-8'), 'Текстовый слой PDF 2026')

    def test_extraction_refuses_non_image_and_pdf(self):
        import base64
        manager = TaskManager(Host(self.root / 'bad-ocr-workspace'))
        with self.assertRaisesRegex(ValueError, 'изображения и PDF'):
            manager.create_text_extraction([{'name':'audio.wav','b64':base64.b64encode(b'no').decode()}])

    def test_lira_text_extraction_command_routes_before_model(self):
        import base64
        image = base64.b64encode(b'png-data').decode()
        harness = ExtractionCommandHarness()
        result = harness._command('say', {'text':'Лира, извлечь текст', 'files':[{'name':'scan.png','b64':image}]})
        self.assertEqual(result['id'], 'ocr-task')
        self.assertEqual(harness.tasks.seen['files'][0]['name'], 'scan.png')
        self.assertIn('результатах задачи', harness.quick[0][0])

    def test_markdown_html_docx_pdf_and_spreadsheet_roundtrip(self):
        (self.root / "guide.md").write_text("# Заголовок\n\nТекст с **выделением**.\n", encoding="utf-8")
        html_result = self.convert("guide.md", "html")["path"].read_text(encoding="utf-8")
        self.assertIn("<h1>Заголовок</h1>", html_result)
        docx = self.convert("guide.md", "docx")["path"]
        self.assertEqual(zipfile.ZipFile(docx).testzip(), None)
        xml = zipfile.ZipFile(docx).read("word/document.xml").decode()
        self.assertIn("Заголовок", xml)
        self.assertIn("Heading1", xml)
        back = FC.convert(docx, "md", self.out)["path"].read_text(encoding="utf-8")
        self.assertIn("# Заголовок", back)
        (self.root / "table.csv").write_text("Код;Название\r\n001;Кабель\r\n002;Панель\r\n", encoding="utf-8")
        xlsx = self.convert("table.csv", "xlsx")["path"]
        self.assertEqual(zipfile.ZipFile(xlsx).testzip(), None)
        roundtrip = FC.convert(xlsx, "csv", self.out)["path"]
        with roundtrip.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows, [["Код", "Название"], ["001", "Кабель"], ["002", "Панель"]])
        # The generated OOXML is readable by LibreOffice without extra Python packages.
        if FC._soffice():
            pdf = FC.convert(xlsx, "pdf", self.out)["path"]
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            docx_pdf = FC.convert(docx, "pdf", self.out)["path"]
            self.assertTrue(docx_pdf.read_bytes().startswith(b"%PDF-"))

    def test_html_table_to_xlsx_and_csv_to_docx_table(self):
        (self.root / "table.html").write_text("<table><tr><th>Артикул</th><th>Кол-во</th></tr><tr><td>A-01</td><td>3</td></tr></table>", encoding="utf-8")
        xlsx = self.convert("table.html", "xlsx")["path"]
        self.assertEqual(FC._xlsx_sheet(xlsx), [["Артикул", "Кол-во"], ["A-01", "3"]])
        (self.root / "table.csv").write_text("Name,Value\nOne,1\n", encoding="utf-8")
        docx = self.convert("table.csv", "docx")["path"]
        body = zipfile.ZipFile(docx).read("word/document.xml").decode()
        self.assertIn("<w:tbl>", body)
        self.assertIn("Name", body)

    def test_requested_document_conversion_matrix_preserves_tables(self):
        from unittest.mock import patch
        table = [["Артикул", "Количество", "Код"], ["A-01", "3", "0012"], ["B-02", "7", "0045"]]
        md = self.root / "таблица.md"
        md.write_text("# Склад\n\nОстатки на сегодня.\n\n" + FC._rows_markdown(table) + "\n", encoding="utf-8")
        docx = self.root / "таблица.docx"
        FC._write_docx([("h1", "Склад"), ("p", "Остатки на сегодня."), ("table", table)], docx)
        xlsx = self.root / "таблица.xlsx"
        FC._build_xlsx(table, xlsx)
        html_path = self.root / "таблица.html"
        html_path.write_text("<!doctype html><meta charset=utf-8><h1>Склад</h1><p>Остатки на сегодня.</p>" + FC._rows_html(table, "Склад"), encoding="utf-8")

        # Run every requested text/document route through the same conversion entry point.
        for source, targets in ((docx, ("md", "txt", "html", "pdf")),
                                (html_path, ("md", "txt", "pdf", "docx")),
                                (xlsx, ("md", "txt", "pdf", "docx", "html"))):
            for target in targets:
                with self.subTest(source=source.suffix, target=target):
                    result = FC.convert(source, target, self.out)["path"]
                    self.assertTrue(result.is_file() and result.stat().st_size > 0)

        # Semantic table exports retain columns, cell order, Cyrillic and leading zeroes.
        for source in (md, docx, html_path, xlsx):
            for target in (("csv",) if source == xlsx else ("xlsx", "csv")):
                with self.subTest(source=source.suffix, target=target):
                    converted = FC.convert(source, target, self.out)["path"]
                    if target == "xlsx":
                        rows = FC._xlsx_sheet(converted)
                    else:
                        with converted.open(encoding="utf-8-sig", newline="") as stream:
                            rows = list(csv.reader(stream))
                    self.assertEqual(rows[:3], table)
        docx_from_md = FC.convert(md, "docx", self.out)["path"]
        self.assertEqual(FC._text_to_table(docx_from_md), table)
        md_html = FC.convert(md, "html", self.out)["path"].read_text(encoding="utf-8")
        self.assertIn("<table>", md_html)
        self.assertIn("<td>0012</td>", md_html)

        # A text-layer PDF exercises all four PDF destinations and keeps extracted text.
        pdf_source = FC.convert(docx, "pdf", self.out)["path"]
        self.assertIn("A-01", FC._write_pdf_text(pdf_source))
        for target in ("md", "txt", "html", "docx"):
            with self.subTest(source=".pdf", target=target):
                converted = FC.convert(pdf_source, target, self.out)["path"]
                self.assertTrue(converted.is_file() and converted.stat().st_size > 0)
        pdf_docx = FC.convert(pdf_source, "docx", self.out)["path"]
        self.assertIn("A-01", FC._docx_text(pdf_docx))

    def test_markdown_tables_are_parsed_with_escaped_delimiters(self):
        source = self.root / "pipes.md"
        source.write_text("| Name | Value |\n| :--- | ---: |\n| Pipe \\| cable | 001 |\n", encoding="utf-8")
        self.assertEqual(FC._text_to_table(source), [["Name", "Value"], ["Pipe | cable", "001"]])
        blocks = FC._markdown_blocks(source.read_text(encoding="utf-8"))
        self.assertEqual(blocks, [("table", [["Name", "Value"], ["Pipe | cable", "001"]])])

    def test_webpage_url_extract_and_ssrf_guards(self):
        self.assertEqual(FC.webpage_url("Лира, преобразуй страницу https://example.com/docs?q=1 в MD"), "https://example.com/docs?q=1")
        self.assertIsNone(FC.webpage_url("Лира, преобразуй документ в MD"))
        for url in ("file:///etc/passwd", "http://127.0.0.1/admin", "http://localhost/"):
            with self.subTest(url=url), self.assertRaises(FC.ConversionError):
                FC._validate_public_web_url(url)
        self.assertIn('docx', FC.targets_for('report.pdf'))
        self.assertIn('docx', FC.targets_for('report.xlsx'))

    def test_lira_page_command_routes_url_to_background_conversion(self):
        harness = WebPageCommandHarness()
        result = harness._command('say', {'text':'Лира, преобразовать страницу https://example.com/guide в MD'})
        self.assertEqual(result['id'], 'page-task')
        self.assertEqual(harness.tasks.seen, ('https://example.com/guide', 'md', 'Лира, преобразовать страницу https://example.com/guide в MD'))
        self.assertIn('HTML-страницу', harness.quick[0][0])

    def test_result_editor_writes_only_declared_text_outputs(self):
        outdir = self.root / 'task' / 'outputs'; outdir.mkdir(parents=True)
        result = outdir / 'result.md'; result.write_text('# Before\n', encoding='utf-8')
        record = ResultRecord(outdir.parent, [{'path':'outputs/result.md'}])
        harness = ResultCommandHarness(record)
        saved = harness._command('task_write_result', {'id':'result-task','path':'outputs/result.md','text':'# After\n'})
        self.assertTrue(saved['saved'])
        self.assertEqual(result.read_text(encoding='utf-8'), '# After\n')
        with self.assertRaisesRegex(ValueError, 'результата'):
            harness._command('task_write_result', {'id':'result-task','path':'../../outside.txt','text':'x'})
        pdf = outdir / 'result.pdf'; pdf.write_bytes(b'%PDF')
        record.data['outputs'].append({'path':'outputs/result.pdf'})
        with self.assertRaisesRegex(ValueError, 'нельзя редактировать'):
            harness._command('task_write_result', {'id':'result-task','path':'outputs/result.pdf','text':'x'})

    def test_html_to_markdown_keeps_page_heading_and_table(self):
        html_source = "<html><body><h1>Склад</h1><p>Проверка страницы</p><table><tr><th>Код</th><th>Кол-во</th></tr><tr><td>001</td><td>2</td></tr></table></body></html>"
        result = FC._html_to_markdown(html_source)
        self.assertIn("# Склад", result)
        self.assertIn("Проверка страницы", result)
        self.assertIn("| Код | Кол-во |", result)
        self.assertIn("| 001 | 2 |", result)

    def test_raster_conversion_including_gif(self):
        from PIL import Image
        first = Image.new("RGBA", (12, 8), (240, 20, 30, 150))
        second = Image.new("RGBA", (12, 8), (20, 40, 230, 255))
        source = self.root / "motion.gif"
        first.save(source, save_all=True, append_images=[second], duration=[80, 120], loop=0, disposal=2)
        png = FC.convert(source, "png", self.out)["path"]
        with Image.open(png) as image: self.assertEqual(image.format, "PNG")
        gif = FC.convert(png, "gif", self.out)["path"]
        with Image.open(gif) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.size, (12, 8))

    def test_audio_mp3_wav_roundtrip(self):
        wav_path = self.root / "tone.wav"
        with wave.open(str(wav_path), "wb") as stream:
            stream.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            frames = b"".join(struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * i / 44100))) for i in range(44100 // 2))
            stream.writeframes(frames)
        mp3 = FC.convert(wav_path, "mp3", self.out)["path"]
        self.assertGreater(mp3.stat().st_size, 500)
        mpeg = FC.convert(wav_path, "mpeg", self.out)["path"]
        self.assertGreater(mpeg.stat().st_size, 500)
        restored = FC.convert(mp3, "wav", self.out)["path"]
        with wave.open(str(restored), "rb") as stream:
            self.assertEqual(stream.getframerate(), 44100)
            self.assertGreater(stream.getnframes(), 10000)

    def test_native_task_parallel_outputs_without_model(self):
        root = self.root / "workspace"
        host = Host(root)
        manager = TaskManager(host)
        payload = lambda name, text: {"name": name, "b64": "data:text/csv;base64," + base64.b64encode(text.encode()).decode()}
        task = manager.create_file_conversion([payload("rows.csv", "a,b\n1,2\n"), payload("rows.csv", "c,d\n3,4\n")], "xlsx")
        deadline = time.time() + 20
        while task.thread and task.thread.is_alive() and time.time() < deadline:
            time.sleep(.05)
        self.assertEqual(task.status, "done", task.error)
        self.assertEqual(task.data.get("workflow"), "file-conversion")
        self.assertEqual(len(task.outputs), 2)
        self.assertEqual(sorted(Path(task.dir, x["path"]).suffix for x in task.outputs), [".xlsx", ".xlsx"])
        self.assertEqual(task.progress, [2, 2])
        self.assertTrue(host.announcements)
        source = root / "large.md"
        source.write_text("# Путь\nПотоковая загрузка работает.\n", encoding="utf-8")
        path_task = manager.create_file_conversion_from_paths([{"name": source.name, "path": source}], "html")
        deadline = time.time() + 20
        while path_task.thread and path_task.thread.is_alive() and time.time() < deadline: time.sleep(.05)
        self.assertEqual(path_task.status, "done", path_task.error)
        self.assertTrue((path_task.dir / path_task.outputs[0]["path"]).read_text(encoding="utf-8").startswith("<!doctype html>"))

    def test_chunk_upload_commands_stage_file_before_task_creation(self):
        harness = UploadHarness(self.root / "workspace")
        data = b"chunked-file-content\n" * 8000
        session = harness._command("file_convert_begin", {"name": "memo.txt", "size": len(data), "target": "md"})
        midpoint = 70_000
        for block in (data[:midpoint], data[midpoint:]):
            reply = harness._command("file_convert_chunk", {"upload_id": session["upload_id"],
                "b64": base64.b64encode(block).decode()})
        self.assertEqual(reply["received"], len(data))
        task = harness._command("file_convert_finish", {"upload_id": session["upload_id"], "goal": "тест"})
        self.assertEqual(task["id"], "chunk-task")
        self.assertEqual(harness.tasks.seen["bytes"], data)
        self.assertEqual(harness.tasks.seen["target"], "md")
        self.assertFalse(Path(harness.tasks.seen["files"][0]["path"]).exists())

    def test_unsupported_pair_fails_clearly(self):
        (self.root / "input.txt").write_text("hello", encoding="utf-8")
        with self.assertRaisesRegex(FC.ConversionError, "не поддерживается"):
            self.convert("input.txt", "gif")


if __name__ == "__main__":
    unittest.main(verbosity=2)
