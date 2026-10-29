"""Mac-пути: вложенное состояние не попадает в слепок как конфигурация и не уносит модели/ID устройства."""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from backup import Backup


class NestedStateBackup(unittest.TestCase):
    def test_encrypted_snapshot_round_trip_restores_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, state, workspace = root / "a/config", root / "a/config/state", root / "a/workspace"
            for path in (config, state / "days", workspace / "Заметки"):
                path.mkdir(parents=True, exist_ok=True)
            (config / "settings.json").write_text("{}")
            (state / "conversation.json").write_text('{"summary":"remember"}')
            (state / "chat-archive").mkdir()
            (state / "chat-archive/test.json.gz").write_bytes(b'compressed-private-chat')
            (state / "days/2026-10-26.jsonl").write_text('{"text":"day"}\n')
            (workspace / "Заметки/Мой отчёт.md").write_text("Сохранить память")
            snapshot = Backup(config, state, workspace).make(root / "snapshots", password="test-password")
            target = Backup(root / "b/config", root / "b/config/state", root / "b/workspace")
            target.restore(snapshot["path"], password="test-password", mode="merge")
            self.assertEqual((target.state / "conversation.json").read_text(), '{"summary":"remember"}')
            self.assertEqual((target.state / "chat-archive/test.json.gz").read_bytes(), b'compressed-private-chat')
            self.assertEqual((target.ws / "Заметки/Мой отчёт.md").read_text(), "Сохранить память")
            self.assertTrue((target.state / "days/2026-10-26.jsonl").is_file())

    def test_nested_state_is_not_copied_as_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, state, workspace = root / "config", root / "config/state", root / "workspace"
            (state / "models").mkdir(parents=True)
            workspace.mkdir()
            (config / "settings.json").write_text("{}")
            (state / "models/model.bin").write_bytes(b"large-model")
            (state / "sync.json").write_text('{"device":"another-device"}')
            (state / "reminders.json").write_text("[]")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                Backup(config, state, workspace)._fill(archive, "test-password")
            with zipfile.ZipFile(buffer) as archive:
                names = archive.namelist()
            self.assertIn("state/reminders.json", names)
            self.assertFalse(any(name.startswith("config/state/") for name in names), names)
            self.assertFalse(any("models/" in name or name.endswith("sync.json") for name in names), names)

    def test_closed_snapshot_contains_conversation_and_raw_days(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, state, workspace = root / "config", root / "state", root / "workspace"
            for path in (config, state / "days", workspace):
                path.mkdir(parents=True)
            (state / "conversation.json").write_text('{"summary":"remember"}')
            (state / "chat.jsonl").write_text('{"text":"hello"}\n')
            (state / "days/2026-10-26.jsonl").write_text('{"text":"day"}\n')
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                Backup(config, state, workspace)._fill(archive, "test-password")
            with zipfile.ZipFile(buffer) as archive:
                self.assertIn("state/conversation.json", archive.namelist())
                self.assertIn("state/chat.jsonl", archive.namelist())
                self.assertIn("state/days/2026-10-26.jsonl", archive.namelist())


if __name__ == "__main__":
    unittest.main()
