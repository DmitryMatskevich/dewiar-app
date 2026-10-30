"""Role queues apply independent response-token caps to automatically selected models."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from settings import ROLE_MAX_TOKENS_DEFAULT, Settings  # noqa: E402


class RoleTokenLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(Path(self.tmp.name) / "settings.json")
        profiles = {
            "all": {
                "name": "All roles", "kind": "openai", "base_url": "http://127.0.0.1:1/v1",
                "model": "all", "max_tokens": 8192,
                "roles": ["communication", "planning", "coding", "analytics"],
            }
        }
        self.settings.data["llm"].update({"auto_select": False, "active": "all", "profiles": profiles})

    def test_defaults_are_separate(self):
        self.assertEqual(ROLE_MAX_TOKENS_DEFAULT, {"communication": 1024, "planning": 16384, "coding": 8192, "analytics": 32768})
        self.assertEqual(self.settings.active_llm("communication")["max_tokens"], 1024)
        self.assertEqual(self.settings.active_llm("planning")["max_tokens"], 8192)
        self.assertEqual(self.settings.active_llm("coding")["max_tokens"], 8192)
        self.assertEqual(self.settings.active_llm("analytics")["max_tokens"], 8192)

    def test_saved_role_cap_wins_over_profile_cap_but_never_raises_it(self):
        self.settings.update({"llm": {"role_max_tokens": {"communication": 1536, "planning": 12000}}})
        self.assertEqual(self.settings.active_llm("communication")["max_tokens"], 1536)
        self.assertEqual(self.settings.active_llm("planning")["max_tokens"], 8192)
        with self.assertRaises(ValueError):
            self.settings.update({"llm": {"role_max_tokens": {"coding": 64}}})


if __name__ == "__main__":
    unittest.main()
