"""Регрессии режима «Код»: правки пользователя и проверки после изменения исходников.

Все проекты и настройки временные; команды модели и внешний API не используются.
"""
import os
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
_isolation = tempfile.TemporaryDirectory()
os.environ.setdefault("DEWIAR_CONFIG_DIR", str(Path(_isolation.name) / "config"))
os.environ.setdefault("DEWIAR_STATE_DIR", str(Path(_isolation.name) / "state"))
from codework import CodeError, CodeWork
from tasks import TaskManager


class CopySafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "a.txt").write_text("base")
        (self.project / "b.txt").write_text("base b")
        self.code = CodeWork(self.project, self.root / "task" / "copy")
        self.code.prepare()

    def test_user_only_changes_are_not_agent_changes(self):
        (self.project / "a.txt").write_text("user")
        (self.project / "new.txt").write_text("user new")
        self.assertEqual(self.code.changes(), [])

    def test_conflict_prevents_all_writes(self):
        self.code.write("a.txt", "agent")
        self.code.write("b.txt", "agent b")
        (self.project / "b.txt").write_text("user b")
        with self.assertRaises(CodeError):
            self.code.accept()
        self.assertEqual((self.project / "a.txt").read_text(), "base")
        self.assertEqual((self.project / "b.txt").read_text(), "user b")
        self.assertTrue(self.code.work.exists())

    def test_user_creation_is_not_overwritten(self):
        self.code.write("new.txt", "agent")
        (self.project / "new.txt").write_text("user")
        with self.assertRaises(CodeError):
            self.code.accept()
        self.assertEqual((self.project / "new.txt").read_text(), "user")

    def test_unrelated_user_changes_survive_accept(self):
        self.code.write("a.txt", "agent")
        (self.project / "b.txt").write_text("user b")
        self.code.accept()
        self.assertEqual((self.project / "a.txt").read_text(), "agent")
        self.assertEqual((self.project / "b.txt").read_text(), "user b")

    def test_delete_uses_trash(self):
        (self.code.work / "a.txt").unlink()
        removed = []
        def trash(plan):
            for item in plan:
                p = Path(item["path"])
                removed.append(p.name)
                p.rename(self.root / "trashed.txt")
        with patch("guard.trash", side_effect=trash):
            self.code.accept()
        self.assertEqual(removed, ["a.txt"])
        self.assertEqual((self.root / "trashed.txt").read_text(), "base")

    def test_symlink_parent_is_rejected(self):
        self.code.write("sub/new.txt", "agent")
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / "sub").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(CodeError):
            self.code.accept()
        self.assertFalse((outside / "new.txt").exists())


class TestGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        project = root / "project"
        project.mkdir()
        (project / "a.py").write_text("value = 1\n")
        self.code = CodeWork(project, root / "task" / "copy")
        self.code.prepare()
        self.manager = object.__new__(TaskManager)
        self.manager.host = SimpleNamespace(code_auto_run=lambda: True)
        self.manager._q = lambda t: Counter()
        self.manager._opt = lambda *a: False
        self.task = SimpleNamespace(data={"verified": True}, dir=root / "task",
                                    code=lambda: self.code, journal=lambda *a: None)
        self.command = patch("codework.test_command", return_value="run-project-tests")
        self.command.start()
        self.addCleanup(self.command.stop)

    def finish(self):
        return self.manager._finish_gates(self.task, "Исправлено")

    def test_edit_after_pass_requires_new_test(self):
        with patch.object(self.code, "run", return_value="Код завершения 0\nOK") as run:
            self.finish()
            self.code.write("a.py", "value = 2\n")
            self.finish()
        self.assertEqual(run.call_count, 2)

    def test_repeated_failure_is_never_a_pass(self):
        with patch.object(self.code, "run", return_value="Код завершения 1\nFAILED"):
            self.finish()
            self.finish()
            self.finish()
        self.assertEqual(self.task.data.get("code_validation", {}).get("status"), "failed")
        self.assertTrue(self.task.data.get("verification_failed"))

    def test_unchanged_state_reuses_success(self):
        with patch.object(self.code, "run", return_value="Код завершения 0\nOK") as run:
            self.finish()
            self.finish()
        self.assertEqual(run.call_count, 1)

    def test_changed_command_requires_new_check(self):
        with patch.object(self.code, "run", return_value="Код завершения 0\nOK") as run:
            self.finish()
            with patch("codework.test_command", return_value="different-check"):
                self.finish()
        self.assertEqual(run.call_count, 2)

    def test_review_is_invalidated_by_an_edit(self):
        self.task.data.update(critic_done=True, review_fingerprint=self.code.fingerprint())
        self.code.write("a.py", "value = 3\n")
        with patch.object(self.code, "run", return_value="Код завершения 0\nOK"):
            self.finish()
        self.assertFalse(self.task.data["critic_done"])

    def test_failed_finish_saves_explicit_failure(self):
        self.task.title = "Проверка"
        self.task.plan = lambda: ""
        with patch.object(self.code, "run", return_value="Код завершения 1\nFAILED"):
            self.manager._tool_raw(self.task, "finish", {"summary": "Исправлено"})
            self.manager._tool_raw(self.task, "finish", {"summary": "Исправлено"})
        self.assertTrue(self.task.data["verification_failed"])
        self.assertIn("Проверка кода не пройдена", (self.task.dir / "итог.md").read_text())

    def test_force_finish_does_not_bypass_tests(self):
        self.task.data["force_finish"] = True
        with patch.object(self.code, "run", return_value="Код завершения 1\nFAILED") as run:
            self.finish()
        self.assertEqual(run.call_count, 1)
        self.assertTrue(self.task.data.get("verification_failed"))

    def test_test_modifying_source_is_not_a_pass(self):
        def run(*args):
            self.code.write("a.py", "value = 9\n")
            return "Код завершения 0\nOK"
        with patch.object(self.code, "run", side_effect=run):
            result = self.finish()
        self.assertIsNotNone(result)
        self.assertNotEqual(self.task.data.get("code_validation", {}).get("status"), "passed")


if __name__ == "__main__":
    unittest.main()
