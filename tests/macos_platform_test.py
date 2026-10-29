"""Пути и защита macOS; системные файлы не изменяются."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import platform_info
import guard
import settings


class MacPlatform(unittest.TestCase):
    def test_workspace_with_symlinked_parent(self):
        from workspace import Workspace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            actual = root / "actual"
            actual.mkdir()
            (root / "alias").symlink_to(actual, target_is_directory=True)
            with patch.dict(os.environ, {"DEWIAR_WORKSPACE": str(root / "alias")}):
                ws = Workspace(None)
                rel = ws.diary("Тестовая запись", "Dewiar")
                self.assertTrue((actual / rel).is_file())

    def test_persistent_paths(self):
        with patch.object(platform_info, "WIN", False), patch.object(platform_info, "MAC", True):
            config, state = platform_info.data_paths("/example")
        self.assertEqual(str(config), "/example/Library/Application Support/Dewiar")
        self.assertEqual(state, config / "state")
        self.assertNotIn("Caches", state.parts)

    @unittest.skipUnless(sys.platform == "darwin", "проверка реальных ссылок macOS")
    def test_system_paths_are_protected_after_resolve(self):
        for path in ("/etc/passwd", "/private/etc/passwd", "/System/Library", "/Library", "/Applications", "/Users"):
            with self.subTest(path=path):
                self.assertTrue(guard.check_path(path))

    @unittest.skipUnless(sys.platform == "darwin", "проверка команд macOS")
    def test_destructive_system_commands(self):
        for command in ("diskutil eraseDisk APFS X disk9", "sudo /usr/sbin/diskutil eraseVolume APFS X disk9",
                        "csrutil disable", "tmutil delete /backup", "launchctl bootout gui/501"):
            with self.subTest(command=command):
                self.assertEqual(guard.check_command(command)["level"], "block")

    @unittest.skipUnless(sys.platform == "darwin", "проверка Mac")
    def test_os_name_is_macos(self):
        self.assertTrue(settings.OS_NAME.startswith("macOS "))

    @unittest.skipUnless(sys.platform == "darwin", "проверка Mac")
    def test_own_temp_file_can_be_trashed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "note.md"
            path.write_text("own temporary note")
            self.assertFalse(guard.check_path(path))
            with patch("macsys.trash") as trash:
                guard.trash(guard.plan_delete([path]))
            trash.assert_called_once()


if __name__ == "__main__":
    unittest.main()
