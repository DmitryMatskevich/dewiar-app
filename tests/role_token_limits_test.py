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
        self.assertEqual(ROLE_MAX_TOKENS_DEFAULT, {"communication": 1024, "planning": 16384, "coding": 8192, "analytics": 32768, "images": 4096})
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

    def test_manual_selection_is_authoritative_and_auto_mode_uses_roles(self):
        profile = {"name":"Codex subscription", "kind":"codex_subscription", "model":"gpt-test",
                   "roles":["communication"], "max_tokens":8192, "priority":1}
        specialist = {"name":"Analytics specialist", "kind":"openai", "base_url":"http://localhost:2/v1",
                      "model":"analyst", "roles":["analytics"], "priority":1}
        general = {"name":"Other chat model", "kind":"openai", "base_url":"http://localhost:1/v1",
                   "model":"other", "roles":["communication"], "priority":1}
        profiles = {"general":profile,"other":general,"analyst":specialist}
        self.settings.data["llm"].update({"active":"general", "profiles":profiles, "auto_select":False})
        selected = self.settings.active_llm("analytics")
        self.assertEqual(selected["model"], "gpt-test")
        self.assertEqual(selected["_llm_role"], "analytics")
        self.settings.data["llm"]["auto_select"] = True
        automatic = self.settings.active_llm("analytics")
        self.assertEqual(automatic["model"], "analyst")
        self.assertEqual(automatic["_llm_role"], "analytics")
        del profiles["analyst"]
        automatic_fallback = self.settings.active_llm("analytics")
        self.assertEqual(automatic_fallback["model"], "gpt-test")


if __name__ == "__main__":
    unittest.main()
