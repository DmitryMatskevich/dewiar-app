"""Focused offline tests for triage, account defaults, draft revisions and one-shot sending."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="dewiar-mail-service-test-"))
os.environ["DEWIAR_CONFIG_DIR"] = str(TMP / "config")
os.environ["DEWIAR_STATE_DIR"] = str(TMP / "state")
sys.path.insert(0, str(ROOT / "core"))

from mail_service import MailService, MailServiceError  # noqa: E402


class FakeMail:
    def __init__(self, address):
        self.data = {"address": address, "smtp_host": "localhost", "smtp_port": 2525,
                     "imap_host": "localhost", "imap_port": 1143, "provider": "test"}
        self.sent = []
        self.latest = []

    def configured(self):
        return True

    def public(self):
        return {**self.data, "configured": True, "has_password": True}

    def inbox(self, **kwargs):
        return self.latest

    def read(self, uid):
        return {"uid": uid, "from": "Client <client@example.org>", "reply_to": "reply@example.org",
                "to": "Dmitry <d@dcnt.ru>", "cc": "Colleague <colleague@example.org>, dm@sklad-podolsk.ru",
                "subject": "Новая заявка", "date": "Mon, 02 Nov 2026 10:00:00 +0300",
                "message_id": "<original@example.org>", "references": "<older@example.org>",
                "text": "Здравствуйте!\nПодскажите цену.", "files": ["brief.pdf"]}

    def send_reply(self, payload):
        self.sent.append(payload)
        return {"sent_copy": "appended"}

    def uidvalidity(self, folder="INBOX"):
        return "77"


class MailServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mail-service-")
        root = Path(self.tmp.name)
        self.service = MailService(rules_path=root / "rules.json", index_path=root / "index.sqlite3",
                                   account_dir=root / "accounts")
        self.initial_accounts = {a["id"]: a for a in self.service.accounts()}
        self.box = FakeMail("d@dcnt.ru")
        self.service._accounts["dcnt"] = self.box
        for spec in self.service.rules["accounts"]:
            spec["enabled"] = True

    def tearDown(self):
        self.tmp.cleanup()

    def test_account_defaults_include_user_servers_without_password(self):
        accounts = self.initial_accounts
        self.assertEqual(accounts["dcnt"]["address"], "d@dcnt.ru")
        self.assertEqual(accounts["dcnt"]["smtp_host"], "smtp.beget.com")
        self.assertEqual(accounts["dcnt"]["smtp_security"], "starttls")
        self.assertEqual(accounts["podolsk"]["imap_host"], "imap.beget.com")
        self.assertEqual(accounts["mailru"]["smtp_host"], "smtp.mail.ru")
        self.assertFalse(accounts["dcnt"]["enabled"])
        self.assertFalse(accounts["dcnt"]["has_password"])

    def test_classification_handles_self_warehouse_and_fail_closed_form(self):
        self.assertFalse(self.service.classify({"from": "Dmitry <dm@sklad-podolsk.ru>"})[0])
        important, reasons = self.service.classify({"from": "Client <client@example.org>",
            "to": "d@dcnt.ru", "account_id": "podolsk", "received_address": "dm@sklad-podolsk.ru"})
        self.assertTrue(important)
        self.assertIn("Склад в Подольске", reasons)
        self.assertFalse(self.service.classify({"from": "client@example.org", "to": "d@dcnt.ru",
            "subject": "anything", "reply_to": "client@example.org"})[0])

    def test_rules_reject_broad_enabled_form(self):
        rules = json.loads(json.dumps(self.service.rules))
        rules["website_forms"][0].update(enabled=True)
        with self.assertRaisesRegex(MailServiceError, "отправителя или начало темы"):
            self.service.save_rules(rules)

    def test_message_index_deduplicates_and_open_uses_uidvalidity(self):
        row = {"uid": "42", "from": "Client <client@example.org>", "to": "d@dcnt.ru",
               "subject": "Заявка", "date": "Mon, 02 Nov 2026 10:00:00 +0300", "message_id": "<m1>"}
        with self.service._db() as db:
            self.service._store_message(db, self.service.rules["accounts"][0], row, "77", "INBOX")
            self.service._store_message(db, self.service.rules["accounts"][0], row, "77", "INBOX")
        rows = self.service.list_mail()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "dcnt:77:42")
        detail = self.service.open_mail(rows[0]["id"])
        self.assertEqual(detail["text"], "Здравствуйте!\nПодскажите цену.")
        with self.assertRaises(MailServiceError):
            self.service.open_mail("dcnt:76:42")

    def test_reply_all_addresses_signature_revision_confirmation_and_one_shot(self):
        self.service.rules["signature_by_account"]["dcnt"] = "С уважением, Дмитрий"
        key = "dcnt:77:42"
        with self.service._db() as db:
            self.service._store_message(db, self.service.rules["accounts"][0],
                {"uid": "42", "from": "Client <client@example.org>", "to": "d@dcnt.ru",
                 "subject": "Заявка", "date": "Mon, 02 Nov 2026 10:00:00 +0300", "message_id": "<original@example.org>"}, "77", "INBOX")
        with patch.object(self.service, "open_mail", return_value={**self.box.read("42"), "account_id": "dcnt"}):
            draft = self.service.reply_prepare(key, "Ответ")
        self.assertEqual(draft["to"], ["reply@example.org"])
        self.assertEqual(draft["cc"], ["colleague@example.org", "d@dcnt.ru"])
        self.assertIn("С уважением, Дмитрий", draft["body"])
        self.assertIn("> Подскажите цену.", draft["body"])
        confirmation = self.service.reply_confirmation(draft["id"], 1)
        self.assertIn(draft["body"], confirmation)
        revised = self.service.reply_revise(draft["id"], 1, {"body": "Исправленный ответ"})
        self.assertEqual(revised["revision"], 2)
        with self.assertRaises(MailServiceError):
            self.service.reply_confirmation(draft["id"], 1)
        sent = self.service.reply_send(draft["id"], 2)
        self.assertEqual(sent["sent_copy"], "appended")
        self.assertEqual(len(self.box.sent), 1)
        with self.service._db() as db:
            self.assertEqual(db.execute("SELECT answered FROM messages WHERE account_id='dcnt' AND uid='42'").fetchone()[0], 1)
        with self.assertRaises(MailServiceError):
            self.service.reply_send(draft["id"], 2)
        self.assertEqual(len(self.box.sent), 1)

    def test_linked_newer_message_stops_reply(self):
        key = "dcnt:77:42"
        with patch.object(self.service, "open_mail", return_value={**self.box.read("42"), "account_id": "dcnt"}):
            draft = self.service.reply_prepare(key, "Ответ")
        self.box.latest = [{"in_reply_to": "<original@example.org>", "references": "", "date": "Mon, 02 Nov 2026 12:00:00 +0300"}]
        with self.assertRaisesRegex(MailServiceError, "появилось новое письмо"):
            self.service.reply_send(draft["id"], 1)
        self.assertEqual(self.box.sent, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
