"""Whole exchanges, compressed lazy archives and failure-safe persistence."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from chatlog import ChatLog


class ChatArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'chat.jsonl'
        self.chat = ChatLog(self.path)
        self.q1 = self.chat.add('user', 'Проверь йод и ёлку')
        self.a1 = self.chat.add('assistant', 'Первый ответ')
        self.a2 = self.chat.add('assistant', 'Второй самостоятельный ответ')
        self.t1 = self.chat.active
        self.t2 = self.chat.select()
        self.q2 = self.chat.add('user', 'Другой вопрос')
        self.chat.add('assistant', 'Другой ответ')

    def test_archive_complete_exchange_and_restore_after_restart(self):
        self.assertEqual(self.chat.manage(self.t1, 'archive')['count'], 3)
        self.assertEqual(len(self.chat.page()['items']), 2)
        restarted = ChatLog(self.path)
        with patch.object(restarted, '_read_archive', side_effect=AssertionError('Must not load archive')):
            self.assertEqual(len(restarted.page()['items']), 2)
        archived = restarted.page(archived=True)['items']
        self.assertEqual(len(archived), 3)
        self.assertEqual({x['dialog_id'] for x in archived}, {self.t1})
        restarted.manage(self.t1, 'restore', True)
        self.assertEqual(len(restarted.page()['items']), 5)
        self.assertEqual(restarted.page(archived=True)['items'], [])
        self.assertEqual(restarted.page()['items'][1]['dialog_id'], self.t1)

    def test_multiple_questions_and_saved_context_move_together(self):
        self.chat.select(self.t1)
        self.chat.add('user', 'Ещё один вопрос в этой беседе')
        self.chat.add('assistant', 'Ещё один ответ')
        context = self.chat.context_path()
        context.write_text('{"summary":"private summary","history":[]}', encoding='utf-8')
        self.assertEqual(self.chat.manage(self.t1, 'archive')['count'], 5)
        self.assertFalse(context.exists())
        self.assertEqual(len(self.chat.page(chat_id=self.t2)['items']), 2)
        self.chat.manage(self.t1, 'restore', True)
        self.assertEqual(json.loads(context.read_text())['summary'], 'private summary')
        self.assertEqual(len(self.chat.page(chat_id=self.t1)['items']), 5)

    def test_switching_engine_restores_only_selected_context(self):
        from engine import Engine
        from conversation import Conversation
        from types import SimpleNamespace
        from unittest.mock import Mock
        engine = Engine.__new__(Engine)
        engine.state = 'idle'
        engine.chatlog = self.chat
        engine.conv = Conversation(self.chat.context_path())
        engine.history = [{'role':'user','content':'Only second chat context'}]
        engine.conv.touch(engine.history)
        engine.s = SimpleNamespace(get=lambda *args: 28800)
        engine.emit = Mock()
        engine._chat_select(self.t1)
        self.assertEqual(len(engine.history), 3)
        self.assertTrue(all('Only second chat' not in m['content'] for m in engine.history))
        engine.history = [{'role':'user','content':'Only first chat context'}]
        engine.conv.touch(engine.history)
        engine._chat_select(self.t2)
        self.assertEqual(engine.history[0]['content'], 'Only second chat context')
        engine.conv._busy = True
        with self.assertRaises(ValueError):
            engine._chat_select()

    def test_search_normalization_pagination_and_archive_exclusion(self):
        self.assertEqual(self.chat.page(q='И\u0306ОД')['items'][0]['id'], self.q1['id'])
        self.assertTrue(self.chat.page(limit=2)['more'])
        self.assertEqual(len(self.chat.page(before=self.q2['id'])['items']), 3)
        self.chat.manage(self.t1, 'archive')
        self.assertEqual(self.chat.page(q='ёлку')['items'], [])
        self.assertEqual(len(self.chat.page(q='ЁЛКУ', archived=True)['items']), 1)

    def test_delete_removes_only_selected_exchange(self):
        self.chat.manage(self.t1, 'delete')
        self.assertEqual(len(ChatLog(self.path).items), 2)
        self.assertEqual(self.chat.page(archived=True)['items'], [])
        self.chat.manage(self.t2, 'archive')
        self.chat.manage(self.t2, 'delete', True)
        self.assertEqual(self.chat.page(archived=True)['items'], [])

    def test_write_failure_preserves_active_data_and_recovery_archive(self):
        with patch.object(self.chat, '_write_all', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                self.chat.manage(self.t1, 'archive')
        self.assertEqual(len(self.chat.items), 5)
        self.assertEqual(len(ChatLog(self.path).items), 5)
        # A completed recovery archive must not duplicate active messages in the UI.
        self.assertEqual(self.chat.page(archived=True)['items'], [])

    def test_restore_failure_keeps_archive(self):
        self.chat.manage(self.t1, 'archive')
        with patch.object(self.chat, '_write_all', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                self.chat.manage(self.t1, 'restore', True)
        self.assertEqual(len(self.chat.items), 2)
        self.assertEqual(len(self.chat.page(archived=True)['items']), 3)

    def test_unknown_id_and_traversal_do_not_touch_other_files(self):
        for action, archived in [('archive', False), ('delete', False), ('delete', True), ('restore', True)]:
            with self.assertRaises(ValueError):
                self.chat.manage('../../chat', action, archived)
        self.assertEqual(len(self.chat.items), 5)

    def test_archive_really_compresses_and_clear_keeps_it(self):
        self.chat.add('user', 'Длинный вопрос ' * 800)
        q = self.chat.items[-1]['id']
        self.chat.add('assistant', 'Длинный ответ ' * 800)
        size = len(json.dumps(self.chat.items[-2:], ensure_ascii=False).encode())
        self.chat.manage(self.t2, 'archive')
        self.assertLess(self.chat._archive_path(self.t2).stat().st_size, size // 4)
        self.chat.clear()
        self.assertEqual(len(self.chat.page(archived=True)['items']), 4)


if __name__ == '__main__':
    unittest.main()
