"""Worker launch and error propagation without network, downloaded weights or account credentials."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
import model_download as M


class ModelDownload(unittest.TestCase):
    def run_worker(self, frozen, ok):
        with tempfile.TemporaryDirectory() as tmp:
            def spawn(command, **kwargs):
                self.assertEqual("--download-gigaam" in command, frozen)
                self.assertIn("gigaam-v3-e2e-rnnt", command)
                report = Path(command[-1])
                report.write_text(json.dumps({"ok": ok, "error": "fixture download failed"}))
                return SimpleNamespace(returncode=0 if ok else 1)
            with patch.object(M, "STATE_DIR", Path(tmp)), patch.object(sys, "frozen", frozen, create=True), patch.object(M.subprocess, "run", side_effect=spawn):
                if ok:
                    self.assertTrue(M.download_gigaam("gigaam-v3-e2e-rnnt")["ok"])
                else:
                    with self.assertRaisesRegex(RuntimeError, "fixture download failed"):
                        M.download_gigaam("gigaam-v3-e2e-rnnt")

    def test_frozen_uses_worker_entry_point(self):
        self.run_worker(True, True)

    def test_source_uses_worker_script(self):
        self.run_worker(False, True)

    def test_worker_failure_is_not_success(self):
        self.run_worker(True, False)

    def test_worker_does_not_change_core_privacy_state(self):
        import egress
        with patch.dict(egress._state, {"on": True}):
            self.run_worker(False, True)
            self.assertTrue(egress.local_only())


if __name__ == "__main__":
    unittest.main()
