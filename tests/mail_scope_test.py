"""Mail view context is short-lived and contains message headers, never message bodies."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import mail_scope  # noqa: E402


class MailScopeTests(unittest.TestCase):
    def test_active_mail_scope_exposes_selection_and_visible_headers_only(self):
        class Engine:
            pass

        engine = Engine()
        mail_scope.command(engine, "mail_scope_update", {
            "active": True, "view": "unread", "account_id": "dcnt",
            "selected": {"id": "dcnt:77:12", "thread_id": "<root@example.org>",
                         "from": "client@example.org", "subject": "Вопрос", "date": "today",
                         "account": "d@dcnt.ru", "body": "must not be retained"},
            "messages": [{"id": "dcnt:77:12", "thread_id": "<root@example.org>", "subject": "Вопрос",
                          "body": "must not be retained"}],
        })

        prompt = mail_scope.context(engine)
        self.assertIn("dcnt:77:12", prompt)
        self.assertIn("<root@example.org>", prompt)
        self.assertNotIn("must not be retained", prompt)
        self.assertIn("mail_reply action=prepare", mail_scope.system_note())

    def test_leaving_mail_clears_assistant_mail_context(self):
        class Engine:
            pass

        engine = Engine()
        mail_scope.command(engine, "mail_scope_update", {"active": True})
        mail_scope.command(engine, "mail_scope_update", {"active": False})
        self.assertEqual(mail_scope.context(engine), "")


if __name__ == "__main__":
    unittest.main()
