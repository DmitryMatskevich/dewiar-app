"""Local extraction of named contact details from fetched HTML pages."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))

from tasks import TaskManager
from web_contacts import extract


PAGE = '''<!doctype html><html><head><title>Мастерская Пример</title></head><body>
<h1>Контакты</h1><p>hello@example.org</p>
<a href="tel:+7-900-123-45-67">Позвонить</a>
<a href="mailto:sales@example.org">Написать</a>
<a href="https://vk.com/example">ВКонтакте</a>
<a href="https://t.me/example">Telegram</a>
<script>bad@example.net</script></body></html>'''


class Workspace:
    def __init__(self, root): self.root = Path(root)
    def ensure(self): self.root.mkdir(parents=True, exist_ok=True); return self.root


class Host:
    def __init__(self, root):
        self.workspace = Workspace(root)
        self.stopping = threading.Event(); self.stopping.set()
        self.events = []
    def persona_name(self): return 'Тестовый помощник'
    def emit(self, *args, **kwargs): self.events.append((args, kwargs))


class WebContactsTest(unittest.TestCase):
    def test_parses_title_visible_contacts_and_social_links(self):
        result = extract(PAGE, 'https://example.org/contact')
        self.assertEqual(result['title'], 'Мастерская Пример')
        self.assertEqual(result['contacts']['email'], ['hello@example.org', 'sales@example.org'])
        self.assertEqual(result['contacts']['phone'], ['+7-900-123-45-67'])
        self.assertEqual(result['contacts']['social'], ['https://vk.com/example', 'https://t.me/example'])
        self.assertNotIn('bad@example.net', result['contacts']['email'])

    def test_native_page_tasks_save_html_and_named_contacts_without_models(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = TaskManager(Host(Path(folder) / 'workspace'))
            def fetch(_url, dest, cancel=None):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_text(PAGE, encoding='utf-8')
                return Path(dest)
            with patch('file_converter.fetch_webpage', side_effect=fetch):
                html_task = manager.create_webpage_extraction(['https://example.org/contact'], 'html')
                contacts_task = manager.create_webpage_extraction(['https://example.org/contact'], 'contacts')
                for task in (html_task, contacts_task):
                    deadline = time.time() + 10
                    while task.thread and task.thread.is_alive() and time.time() < deadline: time.sleep(.02)
                    self.assertEqual(task.status, 'done', task.error)
            html_files = list((html_task.dir / 'outputs').glob('*.html'))
            self.assertEqual(len(html_files), 1)
            self.assertIn('<title>Мастерская Пример</title>', html_files[0].read_text(encoding='utf-8'))
            report = (contacts_task.dir / 'outputs/contacts.md').read_text(encoding='utf-8')
            self.assertIn('## Мастерская Пример', report)
            self.assertIn('sales@example.org', report)
            self.assertIn('https://vk.com/example', report)


if __name__ == '__main__':
    unittest.main()
