import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from mac_build_identity import validate_manifest, write_manifest  # noqa: E402


class MacBuildIdentityTest(unittest.TestCase):
    def make_bundle(self, root: Path) -> Path:
        bundle = root / "dist" / "Dewiar.app" / "Contents" / "Resources"
        bundle.mkdir(parents=True)
        return bundle.parent.parent

    def test_fresh_manifest_is_accepted_and_stale_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "core").mkdir()
            source = root / "core" / "marker.py"
            source.write_text("version = 1\n", encoding="utf-8")
            bundle = self.make_bundle(root)
            write_manifest(root, bundle / "Contents/Resources/mac-build-manifest.json", build_id="test-build")

            manifest = validate_manifest(bundle, root)
            self.assertEqual(manifest["build_id"], "test-build")

            source.write_text("version = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "устарел"):
                validate_manifest(bundle, root)

    def test_missing_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.make_bundle(root)
            with self.assertRaisesRegex(ValueError, "нет манифеста"):
                validate_manifest(bundle, root)


if __name__ == "__main__":
    unittest.main()
