"""Снимок памяти: Unicode, старые ID и недоступные источники без настоящего облака."""
import hashlib
import os
import sys
import tempfile
import threading
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from workspace import Workspace
from sync import Sync, SyncError


class SyncSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {"DEWIAR_WORKSPACE": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ws = Workspace(None)
        self.ws.ensure()
        self.sync = object.__new__(Sync)
        self.sync.host = SimpleNamespace(workspace=self.ws, reminders=SimpleNamespace(items=[]), money=SimpleNamespace(items=[]))
        self.sync.state = {"map": {}, "idx": {}, "kinds": []}

    def test_new_note_id_is_based_on_nfc(self):
        name = unicodedata.normalize("NFD", "Мой отчёт.md")
        (self.root / "Заметки" / name).write_text("Заметка")
        snap = self.sync.snapshot()
        expected = "note:f" + hashlib.sha1(unicodedata.normalize("NFC", name).encode()).hexdigest()[:12]
        self.assertIn(expected, snap)

    def test_existing_id_survives_normalization(self):
        nfc, nfd = "Мой отчёт.md", unicodedata.normalize("NFD", "Мой отчёт.md")
        self.sync.state["map"]["legacy123"] = nfc
        (self.root / "Заметки" / nfd).write_text("Заметка")
        self.assertIn("note:legacy123", self.sync.snapshot())
        self.assertEqual(self.sync.note_sid("Заметки/" + nfd), "legacy123")

    def test_vault_id_preserves_existing_identifier(self):
        vault = self.root / "vault"
        vault.mkdir()
        nfc, nfd = "Мой отчёт.md", unicodedata.normalize("NFD", "Мой отчёт.md")
        (vault / nfd).write_text("Заметка")
        self.sync.state["vmap"] = {"old-vault-id": nfc}
        out = {}
        self.sync._snap_vault(out, SimpleNamespace(vault=lambda: vault, mirror=lambda: None))
        self.assertIn("vault:old-vault-id", out)

    def test_unreadable_memory_does_not_become_empty(self):
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.name == "Обо мне.md":
                raise PermissionError("test: access denied")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", new=read):
            with self.assertRaises(SyncError):
                self.sync.snapshot()

    def test_inaccessible_subdirectory_aborts_snapshot(self):
        with patch("os.scandir", side_effect=PermissionError("test: folder unavailable")):
            with self.assertRaises(SyncError):
                self.sync.snapshot()

    def test_missing_source_root_is_not_a_deletion(self):
        self.sync.state["map"] = {"known": "gone.md"}
        original = Path.stat
        def stat(path, *args, **kwargs):
            if path == self.root / "Заметки":
                raise PermissionError("test: unavailable source")
            return original(path, *args, **kwargs)
        with patch.object(Path, "stat", new=stat):
            self.assertFalse(self.sync._really_gone("note:known"))

    def test_mass_deletions_are_stopped(self):
        changes = [{"kind": "note", "id": str(n), "deleted": True} for n in range(5)]
        idx = {"note:" + str(n): {"h": "old"} for n in range(6)}
        with self.assertRaises(SyncError):
            self.sync._check_deletions(changes, idx)

    def test_single_explicit_deletion_is_allowed(self):
        self.sync._check_deletions([{"kind": "note", "id": "a", "deleted": True}], {"note:a": {"h": "old"}})

    def test_unreadable_encrypted_record_does_not_advance_cursor(self):
        from sync import BASE_KINDS
        self.sync.host.credentials = lambda: ("test-token", "", "http://invalid.test")
        self.sync.host.device_name = lambda: "Test Mac"
        self.sync.state.update(rev=7, device="test-device")
        self.sync.path = self.root / "sync-state.json"
        self.sync.status = {}
        self.sync._lock = threading.Lock()
        response = SimpleNamespace(status_code=200, json=lambda: {
            "rev": 9, "kinds": list(BASE_KINDS), "items": [{
                "kind": "note", "id": "encrypted-note", "updated": 1, "data": {"e2e": 1, "c": "encrypted"}
            }]})
        with patch.object(self.sync, "_upload_outputs"), patch.object(self.sync, "_claim_tasks"), patch("httpx.post", return_value=response):
            with self.assertRaises(SyncError):
                self.sync.run_once()
        self.assertEqual(self.sync.state["rev"], 7)

    def test_obsidian_unreadable_directory_aborts_inventory(self):
        import obsidian
        with patch("os.scandir", side_effect=PermissionError("test: folder unavailable")):
            with self.assertRaises(OSError):
                obsidian._files(self.root)

    def test_unicode_collision_in_existing_mapping_is_rejected(self):
        from sync import _path_ids
        name = "Мой отчёт.md"
        with self.assertRaises(SyncError):
            _path_ids({"first": name, "second": unicodedata.normalize("NFD", name)})

    def test_dataless_file_is_not_read_or_deleted(self):
        import stat
        path = self.root / "Заметки/remote.md"
        path.write_text("body must not be read")
        self.sync.state["map"] = {"known": "remote.md"}
        original = Path.lstat
        def lstat(candidate, *args, **kwargs):
            if candidate == path:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_flags=0x40000000)
            return original(candidate, *args, **kwargs)
        with patch.object(Path, "lstat", new=lstat):
            snap = self.sync.snapshot()
        self.assertNotIn("note:known", snap)
        self.assertFalse(self.sync._really_gone("note:known"))

    def test_case_collision_in_existing_mapping_is_rejected_on_mac(self):
        from sync import _path_ids
        with patch("sync.MAC", True), self.assertRaises(SyncError):
            _path_ids({"first": "Plan.md", "second": "plan.md"})


if __name__ == "__main__":
    unittest.main()
