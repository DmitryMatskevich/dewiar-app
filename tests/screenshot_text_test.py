"""Persisted screenshot edits: conflicts, rollback, empty text and exact content."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
from chatlog import ChatLog
from screenshot_messages import command


class ScreenshotTextTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'chat.jsonl'
        self.chat = ChatLog(self.path)
        self.item = self.chat.add('assistant', 'Снимок экрана', att=[{
            'kind': 'image', 'name': 'Снимок', 'path': 'screen.png', 'thumb': 'data:image/png;base64,',
            'capture': {'text': 'Исходный текст', 'engine': 'test', 'status': 'done', 'summary': 'Готово'}}])
        self.engine = SimpleNamespace(chatlog=self.chat, emit=Mock())

    def save(self, text, revision=0):
        return command(self.engine, 'screenshot_text_save', {'id':self.item['id'], 'text':text, 'revision':revision})

    def test_persistence_empty_text_noop_and_conflict(self):
        text = '  😀\n<script>буквально</script>\n'
        first = self.save(text)
        self.assertEqual(first['revision'], 1)
        self.assertEqual(self.save(text, 1), first)
        with self.assertRaisesRegex(ValueError, 'другом окне'):
            self.save('Устаревшая правка', 0)
        reloaded = ChatLog(self.path).screenshot(self.item['id'])
        self.assertEqual(reloaded['att'][0]['capture']['text'], text)
        self.assertTrue(reloaded['att'][0]['capture']['text_edited'])
        self.save('', 1)
        self.assertEqual(ChatLog(self.path).screenshot(self.item['id'])['att'][0]['capture']['text'], '')

    def test_write_failure_rolls_back_memory_and_file(self):
        before = self.path.read_bytes()
        with patch.object(self.chat, '_write_all', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.save('Новые слова')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.chat.screenshot(self.item['id'])['att'][0]['capture']['text'], 'Исходный текст')
        self.assertEqual(self.save('Повторная попытка')['revision'], 1)

    def test_invalid_or_dismissed_message_is_not_changed(self):
        before = self.path.read_bytes()
        for text, revision in [(None, 0), ({}, 0), ('x', True), ('x', -1), ('x', None)]:
            with self.assertRaises(ValueError): self.save(text, revision)
        self.assertEqual(self.path.read_bytes(), before)
        self.chat.screenshot(self.item['id'], dismiss=True)
        with self.assertRaisesRegex(ValueError, 'не найдено'): self.save('Не сохранять')


if __name__ == '__main__': unittest.main()
