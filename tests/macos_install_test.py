"""Installer acceptance on temporary bundles: replacement, rollback, signer safety."""
import plistlib
import shutil
import sys
import tempfile
import unittest
import zipfile
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import install_mac_app as installer


class InstallTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS launch lock')
    def test_cli_refuses_install_while_gui_launch_lock_is_held(self):
        import fcntl
        state = Path(self.temp.name) / 'state'
        state.mkdir()
        with (state / 'ui.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([sys.executable, str(Path(installer.__file__)),
                                     '--source', str(self.source), '--target', str(self.target)],
                                    env={**os.environ, 'DEWIAR_STATE_DIR': str(state)},
                                    capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Dewiar запущен', result.stderr)
        self.assertEqual((self.target / 'Contents/payload').read_text(), 'old')

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
            (bundle / "Contents/payload").write_text(value)

    def copy_or_archive(self, args, **kwargs):
        if '-k' in args:
            with zipfile.ZipFile(args[-1], 'w') as archive:
                root = Path(args[-2])
                for path in root.rglob('*'):
                    if path.is_file():
                        archive.write(path, str(Path(root.name) / path.relative_to(root)))
        else:
            shutil.copytree(args[1], args[2])

    def test_replacement_removes_obsolete_files_and_retains_backup(self):
        (self.target / "Contents/obsolete").write_text("old dependency")
        inode = self.target.stat().st_ino
        with patch.object(installer, "signing", return_value='designated => cdhash H"same"'), \
             patch.object(installer.subprocess, "run", side_effect=self.copy_or_archive):
            installer.install(self.source, self.target)
        self.assertEqual((self.target / "Contents/payload").read_text(), "new")
        self.assertEqual(self.target.stat().st_ino, inode)
        self.assertFalse((self.target / "Contents/obsolete").exists())
        backups = list(self.target.parent.glob(".dewiar-install-*/previous.zip"))
        self.assertEqual(len(backups), 1)
        with zipfile.ZipFile(backups[0]) as archive:
            self.assertEqual(archive.read('Dewiar.app/Contents/payload'), b'old')
        self.assertEqual(list(self.target.parent.glob('.dewiar-install-*/*.app')), [])

    def test_failed_final_rename_restores_previous_app(self):
        rename = Path.rename
        def fail_new(path, target):
            if path.parent.name == "Dewiar.app" and path.parent.parent.name.startswith(".dewiar-install-"):
                raise OSError("injected install failure")
            return rename(path, target)
        with patch.object(installer, "signing", return_value='designated => cdhash H"same"'), \
             patch.object(installer.subprocess, "run", side_effect=self.copy_or_archive), \
             patch.object(Path, "rename", fail_new):
            with self.assertRaises(OSError):
                installer.install(self.source, self.target)
        self.assertEqual((self.target / "Contents/payload").read_text(), "old")

    def test_persistent_signer_cannot_be_downgraded_to_adhoc(self):
        with patch.object(installer, "signing", side_effect=['designated => cdhash H"new"', 'designated => anchor apple generic']), \
             patch.object(installer.subprocess, "run") as copy:
            with self.assertRaises(ValueError):
                installer.install(self.source, self.target)
            copy.assert_not_called()
        self.assertEqual((self.target / "Contents/payload").read_text(), "old")

    def test_failed_installed_signature_restores_previous_contents(self):
        def signature(bundle):
            if bundle == self.target and (bundle / 'Contents/payload').read_text() == 'new':
                raise ValueError('injected signature failure')
            return 'designated => cdhash H"same"'
        inode = self.target.stat().st_ino
        with patch.object(installer, 'signing', side_effect=signature), \
             patch.object(installer.subprocess, 'run', side_effect=self.copy_or_archive):
            with self.assertRaises(ValueError):
                installer.install(self.source, self.target)
        self.assertEqual((self.target / 'Contents/payload').read_text(), 'old')
        self.assertEqual(self.target.stat().st_ino, inode)

    def test_missing_archive_stops_before_replacing_contents(self):
        def copy_only(args, **kwargs):
            if '-k' not in args:
                self.copy_or_archive(args, **kwargs)
        with patch.object(installer, 'signing', return_value='designated => cdhash H"same"'), \
             patch.object(installer.subprocess, 'run', side_effect=copy_only):
            with self.assertRaises(OSError):
                installer.install(self.source, self.target)
        self.assertEqual((self.target / 'Contents/payload').read_text(), 'old')


if __name__ == "__main__":
    unittest.main()
