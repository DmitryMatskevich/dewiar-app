"""Installer acceptance on temporary bundles: replacement, rollback, signer safety."""
import plistlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import install_mac_app as installer


class InstallTests(unittest.TestCase):
    def test_implicit_adhoc_requirement_has_comment_prefix(self):
        self.assertEqual(installer.requirement('Executable=/fixture\n# designated => cdhash H"new"\n'),
                         'designated => cdhash H"new"')
        with self.assertRaises(ValueError):
            installer.requirement('Executable=/fixture')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source, self.target = root / "build/Dewiar.app", root / "installed/Dewiar.app"
        for bundle, value in ((self.source, "new"), (self.target, "old")):
            (bundle / "Contents").mkdir(parents=True)
            (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": installer.BUNDLE_ID}))
            (bundle / "payload").write_text(value)

    def test_replacement_removes_obsolete_files_and_retains_backup(self):
        (self.target / "obsolete").write_text("old dependency")
        with patch.object(installer, "signing", return_value='designated => cdhash H"same"'), \
             patch.object(installer.subprocess, "run", side_effect=lambda args, **kw: shutil.copytree(args[1], args[2])):
            installer.install(self.source, self.target)
        self.assertEqual((self.target / "payload").read_text(), "new")
        self.assertFalse((self.target / "obsolete").exists())
        backups = list(self.target.parent.glob(".dewiar-install-*/previous.app/payload"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "old")

    def test_failed_final_rename_restores_previous_app(self):
        rename = Path.rename
        def fail_new(path, target):
            if path.parent.name.startswith(".dewiar-install-") and path.name == "Dewiar.app":
                raise OSError("injected install failure")
            return rename(path, target)
        with patch.object(installer, "signing", return_value='designated => cdhash H"same"'), \
             patch.object(installer.subprocess, "run", side_effect=lambda args, **kw: shutil.copytree(args[1], args[2])), \
             patch.object(Path, "rename", fail_new):
            with self.assertRaises(OSError):
                installer.install(self.source, self.target)
        self.assertEqual((self.target / "payload").read_text(), "old")

    def test_persistent_signer_cannot_be_downgraded_to_adhoc(self):
        with patch.object(installer, "signing", side_effect=['designated => cdhash H"new"', 'designated => anchor apple generic']), \
             patch.object(installer.subprocess, "run") as copy:
            with self.assertRaises(ValueError):
                installer.install(self.source, self.target)
            copy.assert_not_called()
        self.assertEqual((self.target / "payload").read_text(), "old")


if __name__ == "__main__":
    unittest.main()
