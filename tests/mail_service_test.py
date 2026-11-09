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

from mail_service import MailService, MailServiceError, _message_date_for_quote  # noqa: E402
from tools import TOOLS, t_mail, t_mail_importance  # noqa: E402
import mail as mail_module  # noqa: E402
import mail_keychain  # noqa: E402


class FakeMail:
    def __init__(self, address):
        self.data = {"address": address, "smtp_host": "localhost", "smtp_port": 2525,
                     "imap_host": "localhost", "imap_port": 1143, "provider": "test"}
        self.sent = []
        self.latest = []
        self.trashed = []

    def configured(self):
        return True

    def public(self):
        return {**self.data, "configured": True, "has_password": True}

    def inbox(self, **kwargs):
        return self.latest

    def read(self, uid, folder="INBOX", expected_uidvalidity=""):
        self.last_folder = folder
        return {"uid": uid, "from": "Client <client@example.org>", "reply_to": "reply@example.org",
                "to": "Dmitry <d@dcnt.ru>", "cc": "Colleague <colleague@example.org>, dm@sklad-podolsk.ru",
                "subject": "Новая заявка", "date": "Mon, 02 Nov 2026 10:00:00 +0300",
                "message_id": "<original@example.org>", "references": "<older@example.org>",
                "text": "Здравствуйте!\nПодскажите цену.", "files": ["brief.pdf"]}

    def send_reply(self, payload):
        self.sent.append(dict(payload))
        return {"sent_copy": "appended"}

    def read_raw(self, uid, expected_uidvalidity=""):
        return b"From: client@example.org\r\nSubject: Original\r\n\r\nOriginal body\r\n"

    def move_to_trash(self, uid, expected_uidvalidity=""):
        self.trashed.append(str(uid))
        return {"state": "moved", "folder": "Trash"}

    def special_folder(self, flag):
        return "Sent" if flag == "\\Sent" else ""

    def uidvalidity(self, folder="INBOX"):
        return "88" if folder == "Sent" else "77"


class MailServiceTests(unittest.TestCase):
    def setUp(self):
        self.keychain_patch = patch.object(mail_module.keychain, "exists", return_value=mail_keychain.MISSING)
        self.keychain_patch.start()
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
        self.keychain_patch.stop()

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

    def test_mail_list_collapses_replies_into_one_expandable_conversation(self):
        spec = next(item for item in self.service.rules["accounts"] if item["id"] == "dcnt")
        messages = [
            {"uid": "101", "message_id": "<root@example.org>", "references": "", "in_reply_to": "",
             "from": "Client <client@example.org>", "to": "d@dcnt.ru", "subject": "Запрос цены",
             "date": "Mon, 02 Nov 2026 10:00:00 +0300", "unread": False},
            {"uid": "102", "message_id": "<reply@example.org>", "references": "<root@example.org>",
             "in_reply_to": "<root@example.org>", "from": "Dmitry <d@dcnt.ru>", "to": "client@example.org",
             "subject": "Re: Запрос цены", "date": "Mon, 02 Nov 2026 11:00:00 +0300", "unread": False},
            {"uid": "103", "message_id": "<answer@example.org>",
             "references": "<root@example.org> <reply@example.org> <sent@example.org>", "in_reply_to": "<sent@example.org>",
             "from": "Client <client@example.org>", "to": "d@dcnt.ru", "subject": "Re: Запрос цены",
             "date": "Mon, 02 Nov 2026 12:00:00 +0300", "unread": True},
            {"uid": "104", "message_id": "<sent@example.org>",
             "references": "<root@example.org> <reply@example.org>", "in_reply_to": "<reply@example.org>",
             "from": "Dmitry <d@dcnt.ru>", "to": "client@example.org", "subject": "Re: Запрос цены",
             "date": "Mon, 02 Nov 2026 11:30:00 +0300", "unread": False},
        ]
        with self.service._db() as db:
            for message in messages[:3]:
                self.service._store_message(db, spec, message, "77", "INBOX")
            self.service._store_message(db, spec, messages[3], "88", "SENT")

        conversations = self.service.list_mail(limit=20)
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["subject"], "Re: Запрос цены")
        self.assertEqual(conversations[0]["thread_count"], 4)
        self.assertEqual(conversations[0]["thread_unread_count"], 1)
        self.assertEqual(len(self.service.list_mail(unread=True)), 1)
        thread = self.service.mail_thread(conversations[0]["thread_id"])
        self.assertEqual([item["message_id"] for item in thread],
                         ["<answer@example.org>", "<sent@example.org>", "<reply@example.org>", "<root@example.org>"])
        with patch.object(self.service, "configured", return_value=True):
            thread_text = t_mail({"action": "thread", "thread_id": conversations[0]["thread_id"]}, {"mail": self.service})
        self.assertIn("Здравствуйте!", thread_text)
        self.assertIn("Отправленные", thread_text)
        sent = next(item for item in thread if item["folder"] == "SENT")
        self.service.open_mail(sent["id"])
        self.assertEqual(self.box.last_folder, "Sent")

    def test_importance_rules_match_sender_and_exact_or_partial_subject(self):
        self.service.importance_rule_add(sender_address="client@example.org", importance="important")
        self.service.importance_rule_add(recipient_address="sales@example.org", importance="important")
        self.service.importance_rule_add(subject="Счёт №42", subject_match="exact", importance="normal")
        self.service.importance_rule_add(subject="оплата", subject_match="contains", importance="important")
        self.assertTrue(self.service.classify({"from": "Client <client@example.org>", "subject": "Любая тема"})[0])
        self.assertTrue(self.service.classify({"from": "other@example.org", "to": "Sales <sales@example.org>"})[0])
        self.assertFalse(self.service.classify({"from": "other@example.org", "subject": "СЧЁТ №42"})[0])
        self.assertTrue(self.service.classify({"from": "other@example.org", "subject": "Re: Оплата заказа"})[0])
        self.assertFalse(self.service.classify({"from": "other@example.org", "subject": "Счёт №420"})[0],
                         "Exact subject rule must not match a longer subject")
        self.service.importance_rule_add(sender_address="tb@lanmaster.ru", importance="normal")
        self.assertFalse(self.service.classify({"from": "tb@lanmaster.ru", "subject": "любой"})[0],
                         "An explicit normal rule must override the built-in important sender list")

    def test_importance_rule_update_remove_and_validation(self):
        created = self.service.importance_rule_add(sender_address="client@example.org", importance="important")
        updated = self.service.importance_rule_add(sender_address="CLIENT@example.org", importance="normal")
        self.assertEqual(created["rule"]["id"], updated["rule"]["id"])
        self.assertFalse(self.service.classify({"from": "client@example.org"})[0])
        with self.assertRaisesRegex(MailServiceError, "ровно одно"):
            self.service.importance_rule_add(sender_address="client@example.org", subject="Order")
        self.service.importance_rule_remove(created["rule"]["id"])
        self.assertFalse(self.service.classify({"from": "client@example.org"})[0])

    def test_ai_mail_importance_tool_creates_and_removes_rules(self):
        self.assertIn("mail_importance", TOOLS)
        with patch.object(self.service, "configured", return_value=True):
            response = t_mail_importance({"action": "add", "subject": "заказ", "subject_match": "contains",
                                          "importance": "important"}, {"mail": self.service})
            self.assertIn("применено к уже загруженным письмам", response)
            rule_id = self.service.rules["importance_rules"][0]["id"]
            self.assertTrue(self.service.classify({"from": "buyer@example.org", "subject": "Статус заказа"})[0])
            self.assertIn("удалено", t_mail_importance({"action": "remove", "rule_id": rule_id}, {"mail": self.service}))

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

    def test_server_received_date_overrides_bad_sender_date_for_sorting_and_quotes(self):
        received = 1793945497.0  # 2026-11-06 09:11:37 +0300
        row = {"uid": "43", "from": "Client <client@example.org>", "to": "d@dcnt.ru",
               "subject": "Старое Date", "date": "Sun, 04 Jan 2015 14:24:00 +0300",
               "received_at": received, "message_id": "<bad-date>"}
        with self.service._db() as db:
            self.service._store_message(db, self.service.rules["accounts"][0], row, "77", "INBOX")
            stored = db.execute("SELECT received_at FROM messages WHERE uid='43'").fetchone()[0]
        self.assertEqual(stored, received)
        quoted = _message_date_for_quote(row["date"], received)
        self.assertIn("Получено Fri, 06 Nov 2026 09:11:37 +0300", quoted)
        self.assertIn("заголовке письма: Sun, 04 Jan 2015", quoted)

    def test_reply_draft_to_bad_date_mail_quotes_server_receipt_time(self):
        received = 1793945497.0
        original = {**self.box.read("42"), "account_id": "dcnt", "date": "Sun, 04 Jan 2015 14:24:00 +0300",
                    "received_at": received, "message_id": "<source@example.org>"}
        with patch.object(self.service, "open_mail", return_value=original):
            draft = self.service.reply_prepare("dcnt:77:42", "Проверочный ответ")
        self.assertIn("Проверочный ответ", draft["body"])
        self.assertIn("Получено Fri, 06 Nov 2026 09:11:37 +0300", draft["body"])
        self.assertIn("заголовке письма: Sun, 04 Jan 2015", draft["body"])

    def _index_action_message(self, uid="42"):
        row = {"uid": uid, "from": "Client <client@example.org>", "to": "d@dcnt.ru", "cc": "",
               "subject": "Заявка", "date": "Mon, 02 Nov 2026 10:00:00 +0300", "message_id": "<m-action>"}
        with self.service._db() as db:
            self.service._store_message(db, self.service.rules["accounts"][0], row, "77", "INBOX")

    def test_delete_moves_only_selected_copy_and_removes_local_index_after_success(self):
        self._index_action_message()
        result = self.service.delete_mail("dcnt:77:42")
        self.assertEqual(result, {"state": "moved", "folder": "Trash", "id": "dcnt:77:42"})
        self.assertEqual(self.box.trashed, ["42"])
        self.assertEqual(self.service.list_mail(account_id="dcnt"), [])
        with self.assertRaisesRegex(MailServiceError, "уже удалено"):
            self.service.delete_mail("dcnt:77:42")

    def test_forward_draft_is_editable_and_keeps_original_eml_private(self):
        self._index_action_message()
        draft = self.service.forward_prepare("dcnt:77:42")
        self.assertEqual(draft["kind"], "forward")
        self.assertTrue(draft["forwarded_original"])
        self.assertNotIn("forward_eml_b64", draft)
        self.assertIn("Здравствуйте!", draft["body"])
        with self.assertRaisesRegex(MailServiceError, "Кому"):
            self.service.reply_revise(draft["id"], draft["revision"], {"to": "not-an-address"})
        updated = self.service.reply_revise(draft["id"], draft["revision"],
            {"to": "client@example.org", "cc": "copy@example.org", "subject": "Fwd: Изменённая тема",
             "body": "Пожалуйста, посмотрите."})
        self.assertEqual(updated["to"], ["client@example.org"])
        self.assertEqual(updated["cc"], ["copy@example.org"])
        self.assertNotIn("forward_eml_b64", self.service.draft_get(draft["id"]))
        sent = self.service.reply_send(updated["id"], updated["revision"])
        self.assertEqual(sent["state"], "sent")
        self.assertTrue(self.box.sent[0]["forward_eml_b64"])
        self.assertEqual(self.box.sent[0]["to"], ["client@example.org"])
        with self.service._db() as db:
            stored = db.execute("SELECT payload FROM reply_drafts WHERE id=?", (updated["id"],)).fetchone()[0]
        self.assertNotIn("forward_eml_b64", stored, "drop large forwarded source after one-shot send")

    def test_unread_count_deduplicates_copies_across_accounts(self):
        first = self.service.rules["accounts"][0]
        second = next(x for x in self.service.rules["accounts"] if x["id"] != first["id"])
        self.service.importance_rule_add(sender_address="client@example.org", importance="important")
        unread = {"uid": "42", "from": "Client <client@example.org>", "to": "d@dcnt.ru",
                  "subject": "Новое", "date": "Mon, 02 Nov 2026 10:00:00 +0300", "message_id": "<same>", "unread": True}
        with self.service._db() as db:
            self.service._store_message(db, first, unread, "77", "INBOX")
            self.service._store_message(db, second, {**unread, "uid": "91"}, "88", "INBOX")
            self.service._store_message(db, first, {**unread, "uid": "43", "message_id": "<other>"}, "77", "INBOX")
            self.service._store_message(db, first, {**unread, "uid": "44", "message_id": "<read>", "unread": False}, "77", "INBOX")
        self.assertEqual(self.service.unread_count(), 2)
        self.assertEqual(self.service.important_count(), 3)
        self.assertEqual(self.service.badge_count({}), 3, "no selected filter means all incoming messages")
        self.service.badge_filters_provider = lambda: {"important": True, "unread": False, "unanswered": True}
        self.assertEqual(self.service.badge_count(), 3)
        self.service.badge_filters_provider = lambda: {"important": True, "unread": True, "unanswered": False}
        self.assertEqual(self.service.badge_count(), 2)
        counts = self.service.badge_counts()
        self.assertEqual(counts["badge"], 2)
        self.assertEqual(counts["filters"], {"important": True, "unread": True, "unanswered": False})
        self.assertEqual(counts["attention"], 0)

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
