"""Связка ключей macOS для почты: фоновые операции никогда не показывают окно, как у Apple Mail.

Поддельный Security.framework моделирует главное: запись доступна без вопросов только «своей» сборке
(тому, кто её создал или кому разрешили «всегда»). Чужой сборке macOS показала бы окно — если окна разрешены.
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="dewiar-keychain-test-"))
os.environ.setdefault("DEWIAR_CONFIG_DIR", str(TMP / "config"))
os.environ.setdefault("DEWIAR_STATE_DIR", str(TMP / "state"))
sys.path.insert(0, str(ROOT / "core"))

import mail_keychain as keychain  # noqa: E402


class FakeSecurity(types.ModuleType):
    """Файловая Связка ключей: элементы (service, account) -> {data, owners}; build — текущая сборка Dewiar."""

    def __init__(self, guard=True):
        super().__init__("Security")
        for name in ("kSecClass", "kSecClassGenericPassword", "kSecAttrService", "kSecAttrAccount", "kSecReturnData",
                     "kSecReturnAttributes", "kSecMatchLimit", "kSecMatchLimitOne", "kSecValueData", "kSecAttrLabel",
                     "kSecUseAuthenticationUI", "kSecUseAuthenticationUIFail"):
            setattr(self, name, name)
        self.items, self.build, self.allowed, self.prompts, self.data_reads = {}, "build-1", True, [], 0
        self.user_answer = "always"          # что пользователь нажмёт в окне: always | once | deny
        if guard:
            self.SecKeychainSetUserInteractionAllowed = self._set_ui
            self.SecKeychainGetUserInteractionAllowed = lambda _: (0, self.allowed)

    def _set_ui(self, value):
        self.allowed = bool(value)
        return 0

    def _key(self, q):
        return q[self.kSecAttrService], q[self.kSecAttrAccount]

    def _access(self, item, op):
        """0 — можно; иначе код. Чужой сборке нужно окно: без окон -25308, с окном — ответ пользователя."""
        if self.build in item["owners"]:
            return 0
        if not self.allowed or self.kSecUseAuthenticationUI in self._query:
            return keychain.ERR_INTERACTION_NOT_ALLOWED
        self.prompts.append(op)
        if self.user_answer == "deny":
            return keychain.ERR_USER_CANCELED
        if self.user_answer == "always":
            item["owners"].add(self.build)
        return 0

    def SecItemCopyMatching(self, q, _):
        self._query = q
        item = self.items.get(self._key(q))
        if not item:
            return keychain.ERR_ITEM_NOT_FOUND, None
        if q.get(self.kSecReturnData):
            self.data_reads += 1
            status = self._access(item, "read")
            return (status, item["data"] if status == 0 else None)
        return 0, {"acct": q[self.kSecAttrAccount]}       # атрибуты — без расшифровки и без окна

    def SecItemAdd(self, q, _):
        self._query = q
        if self._key(q) in self.items:
            return keychain.ERR_DUPLICATE_ITEM, None
        self.items[self._key(q)] = {"data": q[self.kSecValueData], "owners": {self.build}}
        return 0, None

    def SecItemUpdate(self, q, attrs):
        self._query = q
        item = self.items.get(self._key(q))
        if not item:
            return keychain.ERR_ITEM_NOT_FOUND
        status = self._access(item, "update")
        if status == 0:
            item["data"] = attrs[self.kSecValueData]
        return status

    def SecItemDelete(self, q):
        self._query = q
        item = self.items.get(self._key(q))
        if not item:
            return keychain.ERR_ITEM_NOT_FOUND
        status = self._access(item, "delete")
        if status == 0:
            del self.items[self._key(q)]
        return status


class KeychainTestCase(unittest.TestCase):
    guard = True

    def setUp(self):
        self.sec = FakeSecurity(guard=self.guard)
        self.patches = [patch.object(keychain.sys, "platform", "darwin"), patch.dict(sys.modules, {"Security": self.sec})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def foreign_item(self, account="d@dcnt.ru", secret=b"old-pass"):
        """Запись, созданная прежней сборкой Dewiar (подпись ad hoc сменилась при пересборке)."""
        self.sec.items[(keychain.SERVICE, account)] = {"data": secret, "owners": {"build-0"}}


class BackgroundNeverPrompts(KeychainTestCase):
    def test_foreign_item_reads_without_window(self):
        self.foreign_item()
        self.assertEqual(keychain.read("d@dcnt.ru"), (keychain.NEEDS_ACCESS, ""))
        self.assertEqual(self.sec.prompts, [])
        self.assertTrue(self.sec.allowed, "разрешение окон возвращено после запроса")

    def test_existence_is_checked_by_attributes(self):
        self.foreign_item()
        self.assertEqual(keychain.exists("d@dcnt.ru"), keychain.OK)
        self.assertEqual(keychain.exists("nobody@dcnt.ru"), keychain.MISSING)
        self.assertEqual((self.sec.prompts, self.sec.data_reads), ([], 0))

    def test_own_item_reads_silently(self):
        self.assertEqual(keychain.write("d@dcnt.ru", "secret"), "d@dcnt.ru")
        self.assertEqual(keychain.read("d@dcnt.ru"), (keychain.OK, "secret"))
        self.assertEqual(keychain.write("d@dcnt.ru", "secret2"), "d@dcnt.ru")
        self.assertEqual(keychain.read("d@dcnt.ru"), (keychain.OK, "secret2"))
        self.assertEqual(self.sec.prompts, [])

    def test_write_over_foreign_item_rotates_without_window(self):
        self.foreign_item()
        ref = keychain.write("d@dcnt.ru", "new-pass")
        self.assertTrue(ref.startswith("d@dcnt.ru#"))
        self.assertEqual(keychain.read(ref), (keychain.OK, "new-pass"))
        self.assertEqual(self.sec.prompts, [])

    def test_explicit_unlock_shows_one_window(self):
        self.foreign_item()
        self.assertEqual(keychain.read("d@dcnt.ru", interactive=True), (keychain.OK, "old-pass"))
        self.assertEqual(self.sec.prompts, ["read"])
        self.sec.user_answer = "deny"
        self.foreign_item("x@dcnt.ru")
        self.assertEqual(keychain.read("x@dcnt.ru", interactive=True)[0], keychain.NEEDS_ACCESS)


class NoGuardMeansNoRead(KeychainTestCase):
    guard = False

    def test_without_ui_switch_secret_is_not_requested(self):
        self.foreign_item()
        self.assertEqual(keychain.read("d@dcnt.ru"), (keychain.NEEDS_ACCESS, ""))
        self.assertEqual((self.sec.prompts, self.sec.data_reads), ([], 0))


class MailBoxLikeAppleMail(KeychainTestCase):
    def setUp(self):
        super().setUp()
        import mail
        self.mail = mail
        self.patches.append(patch.object(mail.sys, "platform", "darwin"))
        self.patches[-1].start()
        self.dir = Path(tempfile.mkdtemp(dir=TMP))

    def box(self, **data):
        path = self.dir / "dcnt.json"
        if data:
            import json
            path.write_text(json.dumps({"address": "d@dcnt.ru", "smtp_host": "smtp.dcnt.ru", "imap_host": "imap.dcnt.ru",
                                        **data}), encoding="utf-8")
        return self.mail.Mail(path)

    def test_startup_and_status_do_not_read_secret(self):
        self.foreign_item()
        box = self.box(provider="x")
        pub = box.public()
        self.assertTrue(pub["configured"] and pub["has_password"])
        self.assertEqual(pub["credential_state"], "saved")
        self.assertNotIn("keychain_account", pub)
        self.assertEqual((self.sec.prompts, self.sec.data_reads), ([], 0))

    def test_legacy_plaintext_migration_writes_only_clean_config(self):
        import json
        path = self.dir / "legacy.json"
        path.write_text(json.dumps({"address": "d@dcnt.ru", "smtp_host": "smtp.dcnt.ru", "password": "legacy-pass"}),
                        encoding="utf-8")
        writes = []
        original = self.mail.Mail._write_config

        def capture(box):
            writes.append(dict(box.data))
            original(box)

        with patch.object(self.mail.Mail, "_write_config", capture):
            box = self.mail.Mail(path)
        self.assertEqual(len(writes), 1)
        self.assertNotIn("password", writes[0])
        self.assertEqual(box._password(), "legacy-pass")
        self.assertNotIn("password", json.loads(path.read_text(encoding="utf-8")))

    def test_new_build_asks_in_app_not_with_system_window(self):
        self.foreign_item()
        box = self.box(provider="x")
        with self.assertRaises(self.mail.MailCredentialError) as ctx:
            box._password()
        self.assertEqual(ctx.exception.state, keychain.NEEDS_ACCESS)
        self.assertIn("введите пароль заново", str(ctx.exception))
        self.assertEqual(box.public()["credential_state"], keychain.NEEDS_ACCESS)
        self.assertEqual(self.sec.prompts, [])

    def test_entering_password_again_fixes_it_for_next_launch(self):
        self.foreign_item()
        box = self.box(provider="x")
        box.save({"address": "d@dcnt.ru", "password": "new-pass"})
        self.assertEqual(box._password(), "new-pass")
        again = self.box()                                   # следующий запуск той же сборки
        self.assertEqual(again._password(), "new-pass")
        self.assertEqual(self.sec.prompts, [])

    def test_secret_is_read_once_per_launch(self):
        self.box(provider="x").save({"address": "d@dcnt.ru", "password": "p"})
        box = self.box()
        reads = self.sec.data_reads
        for _ in range(5):
            box._password()
        self.assertEqual(self.sec.data_reads, reads + 1)

    def test_rejected_password_is_not_retried(self):
        box = self.box(provider="x")
        box.save({"address": "d@dcnt.ru", "password": "wrong"})
        box._auth_rejected(None)
        self.assertEqual(box.public()["credential_state"], "rejected")
        with self.assertRaises(self.mail.MailCredentialError):
            box._password()
        box.save({"address": "d@dcnt.ru", "password": "right"})
        self.assertEqual(box._password(), "right")
        self.assertEqual(box.public()["credential_state"], keychain.OK)

    def test_unlock_moves_secret_to_this_build(self):
        self.foreign_item()
        box = self.box(provider="x")
        self.sec.user_answer = "once"                        # «Разрешить» один раз
        box.unlock()
        self.assertEqual(self.sec.prompts, ["read"])
        self.sec.build = "build-1"
        again = self.box()
        self.assertEqual(again._password(), "old-pass")
        self.assertEqual(self.sec.prompts, ["read"], "следующий запуск — без окна")


class ServiceKeepsOldPasswordWhenNewIsRejected(unittest.TestCase):
    def test_rejected_candidate_is_not_saved(self):
        from mail_service import MailService
        root = Path(tempfile.mkdtemp(dir=TMP))
        service = MailService(rules_path=root / "rules.json", index_path=root / "index.sqlite3", account_dir=root / "accounts")
        spec = service.rules["accounts"][0]
        box = service._accounts[spec["id"]]
        saved = []
        box.save = lambda patch: saved.append(dict(patch)) or {"configured": True}
        box.test = lambda candidate=None: {"smtp": "почта не приняла", "imap": "почта не приняла", "auth_rejected": True}
        result = service.account_save(spec["id"], {"password": "typo"})
        self.assertFalse(result["password_saved"])
        self.assertFalse(any("password" in p for p in saved))
        box.test = lambda candidate=None: {"smtp": "ok", "imap": "ok", "auth_rejected": False}
        result = service.account_save(spec["id"], {"password": "right"})
        self.assertTrue(result["password_saved"])
        self.assertEqual(saved[-1]["password"], "right")

    def test_badge_counts_do_not_touch_keychain(self):
        from mail_service import MailService
        root = Path(tempfile.mkdtemp(dir=TMP))
        service = MailService(rules_path=root / "rules.json", index_path=root / "index.sqlite3", account_dir=root / "accounts")
        with patch.object(keychain, "read", side_effect=AssertionError("Keychain read")), \
                patch.object(keychain, "exists", side_effect=AssertionError("Keychain exists")):
            counts = service.badge_counts()
        self.assertEqual(counts["attention"], 0)


if __name__ == "__main__":
    unittest.main()
