"""Isolated tests for the HTML result editor and HTML image conversions."""
import hashlib
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import file_converter as FC
import html_editor as HE


class Task:
    def __init__(self, root, output):
        self.dir = Path(root)
        self.id = "test-task"
        self.data = {"workflow": "webpage-extraction", "outputs": [output]}
    def save(self):
        pass


class Tasks:
    def __init__(self, task): self.task = task
    def get(self, task_id):
        if task_id != "test-task": raise ValueError("unknown task")
        return self.task


class HtmlEditorConversionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dewiar-html-editor-")
        self.root = Path(self.tmp.name)
        self.output = self.root / "outputs" / "page.html"
        self.output.parent.mkdir(parents=True)
        self.original = "<!doctype html><html><head><title>Тест</title></head><body>" + "<p>содержимое</p>" * 1200 + "</body></html>"
        self.output.write_text(self.original, encoding="utf-8")
        self.task = Task(self.root, {"path": "outputs/page.html", "title": "Тестовая страница"})
        self.tasks = Tasks(self.task)

    def tearDown(self): self.tmp.cleanup()

    def test_load_and_save_are_versioned_without_overwriting_source(self):
        loaded = HE.load_task_html(self.tasks, "test-task", "outputs/page.html")
        self.assertEqual(loaded["html"], self.original)
        self.assertEqual(loaded["revision"], hashlib.sha256(self.original.encode()).hexdigest())
        changed = self.original.replace("Тест", "Изменён")
        saved = HE.save_task_html(self.tasks, "test-task", "outputs/page.html", loaded["revision"], changed)
        self.assertEqual(self.output.read_text(encoding="utf-8"), self.original)
        self.assertEqual((self.root / saved["path"]).read_text(encoding="utf-8"), changed)
        with self.assertRaisesRegex(ValueError, "изменился"):
            HE.save_task_html(self.tasks, "test-task", "outputs/page.html", "stale", changed)

    def test_editor_rejects_unregistered_and_traversal_paths(self):
        for path in ("outputs/missing.html", "../outputs/page.html", str(self.output)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                HE.load_task_html(self.tasks, "test-task", path)

    def test_ai_operations_are_allowlisted(self):
        self.assertEqual(HE._valid_ops([{"op":"set_text", "target":"node-1", "text":"Привет"}], {"node-1"})[0]["text"], "Привет")
        for op in (
            {"op":"delete", "target":"missing"},
            {"op":"set_style", "target":"node-1", "property":"backgroundColor", "value":"url(javascript:alert(1))"},
            {"op":"insert", "tag":"script", "text":"x"},
        ):
            with self.subTest(op=op), self.assertRaises(ValueError): HE._valid_ops([op], {"node-1"})

    def test_html_image_conversion_to_markdown_and_docx(self):
        from PIL import Image
        import io
        image = self.root / "pixel.png"
        stream = io.BytesIO(); Image.new("RGB", (64, 48), "#e89c2c").save(stream, "PNG"); image.write_bytes(stream.getvalue())
        html = self.root / "images.html"
        html.write_text('<!doctype html><html><head><title>Каталог</title></head><body><h1>Каталог LANMASTER</h1><p>Патч-панели</p><img src="pixel.png" alt="Патч-панель"></body></html>', encoding="utf-8")
        md_html = self.root / "images-online.html"
        md_html.write_text('<!doctype html><html><head><base href="https://example.com/catalog/"></head><body><img src="pixel.png" alt="Патч-панель"></body></html>', encoding="utf-8")
        md = FC.convert(md_html, "md", self.root / "converted")["path"].read_text(encoding="utf-8")
        self.assertIn("![Патч-панель](https://example.com/catalog/pixel.png)", md)
        local_md = FC.convert(html, "md", self.root / "local-md")["path"]
        self.assertTrue((local_md.parent / "pixel.png").is_file())
        self.assertIn("![Патч-панель](pixel.png)", local_md.read_text(encoding="utf-8"))
        docx_result = FC.convert(html, "docx", self.root / "converted")
        self.assertEqual(docx_result["warning"], "")
        with zipfile.ZipFile(docx_result["path"]) as package:
            self.assertIsNone(package.testzip())
            self.assertEqual(len([p for p in package.namelist() if p.startswith("word/media/")]), 1)
            rels = package.read("word/_rels/document.xml.rels")
            document = package.read("word/document.xml").decode()
            rel_root = ET.fromstring(rels)
            self.assertEqual(len(rel_root), 1)
            self.assertTrue(rel_root[0].attrib["Type"].endswith("/image"))
            self.assertIn("a:blip", document)
        if FC._soffice():
            word_render = self.root / "word-render"; word_render.mkdir()
            rendered = FC._office_convert(docx_result["path"], "pdf", word_render, None)
            self.assertGreater(rendered.stat().st_size, 1000)

    def test_html_product_card_float_images_left_and_right(self):
        from PIL import Image
        import io
        for name, color in (("left.png", "#d64b3e"), ("right.png", "#3876c7")):
            stream = io.BytesIO(); Image.new("RGB", (320, 220), color).save(stream, "PNG")
            (self.root / name).write_bytes(stream.getvalue())
        html = self.root / "product-card.html"
        html.write_text('''<!doctype html><html><head><meta charset="utf-8"><style>
          .product-photo-left { float:left; width:120px; margin:0 16px 10px 0 }
          .product-photo-right { float:right; width:100px; margin:0 0 10px 16px }
          .wrap-from-parent { float:right }
        </style></head><body>
          <header><nav><a>Каталог</a> <a>Контакты</a></nav></header><main>
          <article class="product-card"><h2>Патч-панель LANMASTER</h2>
          <img class="product-photo-left" src="left.png" alt="Вид спереди">
          <p>Настенная патч-панель для структурированных кабельных систем. Текст описания должен обтекать изображение слева и занимать свободную ширину карточки.</p>
          <table><tr><th>Порты</th><th>Категория</th></tr><tr><td>24</td><td>6</td></tr></table>
          <div class="wrap-from-parent"><img src="left.png" alt="Вид сбоку"></div>
          <p>Дополнительное описание товара должно идти слева от второго изображения, которое выровнено по правой стороне.</p>
          </article></main><footer>LANMASTER · Поддержка и документы</footer>
          </body></html>''', encoding="utf-8")

        markdown = FC.convert(html, "md", self.root / "float-md")["path"].read_text(encoding="utf-8")
        self.assertIn('float:left', markdown)
        self.assertIn('float:right', markdown)
        self.assertIn("обтекать изображение слева", markdown)
        self.assertIn("выровнено по правой стороне", markdown)
        markdown_path = self.root / "float-md" / "product-card.md"
        self.assertTrue((markdown_path.parent / "left.png").is_file())
        markdown_docx = FC.convert(markdown_path, "docx", self.root / "float-md-roundtrip")["path"]
        with zipfile.ZipFile(markdown_docx) as package:
            root = ET.fromstring(package.read("word/document.xml"))
            ns = {"wp":"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}
            self.assertEqual(len(root.findall(".//wp:anchor", ns)), 2)
        markdown_html = FC.convert(markdown_path, "html", self.root / "float-md-roundtrip")["path"]
        self.assertIn("float:left", markdown_html.read_text(encoding="utf-8"))

        docx = FC.convert(html, "docx", self.root / "float-docx")["path"]
        with zipfile.ZipFile(docx) as package:
            self.assertIsNone(package.testzip())
            root = ET.fromstring(package.read("word/document.xml"))
            ns = {"w":"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                  "wp":"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}
            anchors = root.findall(".//wp:anchor", ns)
            self.assertEqual(len(anchors), 2)
            self.assertEqual([a.find("wp:positionH/wp:align", ns).text for a in anchors], ["left", "right"])
            self.assertTrue(all(a.find("wp:wrapSquare", ns).attrib["wrapText"] == "bothSides" for a in anchors))
            self.assertEqual(len(root.findall(".//w:tbl", ns)), 1)
            text = " ".join(node.text or "" for node in root.findall(".//w:t", ns))
            for phrase in ("Каталог", "Патч-панель LANMASTER", "обтекать изображение слева", "Порты", "LANMASTER · Поддержка"):
                self.assertIn(phrase, text)
        if FC._soffice():
            render = self.root / "float-render"; render.mkdir()
            pdf = FC._office_convert(docx, "pdf", render, None)
            self.assertGreater(pdf.stat().st_size, 1000)

    def test_result_action_wiring_and_cross_window_editor_controls(self):
        hud = (ROOT / "ui/hud.html").read_text(encoding="utf-8")
        js = (ROOT / "ui/assets/html-editor.js").read_text(encoding="utf-8")
        self.assertIn("open_html_editor", hud)
        self.assertIn("task_convert_result", hud)
        self.assertIn("html_editor_transfer", js)
        self.assertIn("htmlEditorReceiveTransfer", js)
        self.assertIn("Применить", js)
        for label in ("Шапка сайта", "Меню навигации", "Картинка слева + текст", "Картинка справа + текст",
                      "Карточка товара", "Таблица характеристик", "Подвал сайта"):
            with self.subTest(block=label): self.assertIn(label, js)


if __name__ == "__main__": unittest.main(verbosity=2)
