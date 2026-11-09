"""Multi-account mail triage, header index, user rules and revision-bound reply drafts."""
from __future__ import annotations

import email.utils
import base64
import ipaddress
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from settings import CONFIG_DIR, DATA_DIR, STATE_DIR
from mail import Mail, MailError, guess
import mail_keychain as keychain

log = logging.getLogger("dewiar.mail.service")
RULES_PATH = CONFIG_DIR / "mail-rules.json"
INDEX_PATH = STATE_DIR / "mail-index.sqlite3"
ACCOUNT_DIR = CONFIG_DIR / "mail-accounts"


# Состояния пароля ящика, при которых пользователю нужно ввести пароль или разрешить доступ (значок «!»).
ATTENTION_STATES = {keychain.NEEDS_ACCESS, keychain.LOCKED, "rejected"}
STARTUP_SYNC_DELAY = 15     # секунд: дать окну и модели запуститься, потом проверить почту

class MailServiceError(ValueError):
    pass


def _addr(value):
    return (email.utils.parseaddr(value or "")[1] or "").strip().lower()


def _addresses(value):
    return list(dict.fromkeys(a.lower() for _, a in email.utils.getaddresses([value or ""]) if a and "@" in a))


def _draft_addresses(value, label):
    parts = value if isinstance(value, list) else re.split(r"[;,\s]+", str(value or ""))
    out = []
    for item in parts:
        address = str(item or "").strip().strip("<>").lower()
        if not address:
            continue
        if not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", address):
            raise MailServiceError(f"Проверьте адрес в поле «{label}»: {address}")
        if address not in out:
            out.append(address)
    return out


def _stamp(value):
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return time.time()


def _message_date_for_quote(date_header, received_at):
    """Prefer the server receipt time when a sender's Date header is clearly stale or invalid."""
    try:
        sent = email.utils.parsedate_to_datetime(date_header).timestamp()
        received = float(received_at)
        if abs(received - sent) > 36 * 60 * 60:
            local = time.strftime("%a, %d %b %Y %H:%M:%S %z", time.localtime(received))
            return f"Получено {local} (в заголовке письма: {date_header})"
    except (TypeError, ValueError, OverflowError):
        if received_at:
            return "Получено " + time.strftime("%a, %d %b %Y %H:%M:%S %z", time.localtime(float(received_at)))
    return date_header or ""


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


class MailService:
    """Runtime owner for accounts, cached metadata, background sync and reply drafts."""

    def __init__(self, workspace=None, rules_path=RULES_PATH, index_path=INDEX_PATH, account_dir=ACCOUNT_DIR):
        self.workspace = workspace
        self.rules_path, self.index_path, self.account_dir = Path(rules_path), Path(index_path), Path(account_dir)
        self._lock = threading.RLock()
        self._activated = threading.Event()
        self._activation_lock = threading.Lock()
        self._is_activated = False
        self._accounts = {}
        self._rules_error = ""
        self.rules = self._load_rules()
        self._db_init()
        self._load_accounts()

    def activate(self):
        """Enable mail credential access and background sync after the user opens Mail."""
        if self._is_activated:
            return
        with self._activation_lock:
            if self._is_activated:
                return
            self._migrate_legacy()
            self._is_activated = True
            self._load_accounts()
            self._activated.set()

    # -- rules -----------------------------------------------------------------
    @staticmethod
    def validate_rules(rules):
        if not isinstance(rules, dict) or rules.get("schema_version") != 2:
            raise MailServiceError("Нужен файл правил почты schema_version 2.")
        seen = set()
        accounts = rules.get("accounts", [])
        if not isinstance(accounts, list):
            raise MailServiceError("accounts должен быть списком.")
        for item in accounts:
            if not isinstance(item, dict):
                raise MailServiceError("Каждый ящик должен быть объектом.")
            aid, address = str(item.get("id") or ""), str(item.get("address") or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,40}", aid) or aid in seen:
                raise MailServiceError("У каждого ящика должен быть уникальный id из латинских букв, цифр, _ или -.")
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
                raise MailServiceError(f"Неверный адрес ящика: {address or '(пусто)'}")
            seen.add(aid)
            item["address"] = address
            item["enabled"] = bool(item.get("enabled", False))
            if item.get("sent_copy", "auto") not in ("auto", "append", "off"):
                raise MailServiceError(f"{aid}: sent_copy должен быть auto, append или off.")
        addresses = [x["address"] for x in accounts]
        if len(addresses) != len(set(addresses)):
            raise MailServiceError("Один адрес почты нельзя подключить под разными id.")
        for group_key in ("important_contacts", "important_recipient_groups", "website_forms"):
            group = rules.get(group_key, [])
            if not isinstance(group, list):
                raise MailServiceError(f"{group_key} должен быть списком.")
            ids = set()
            for item in group:
                if not isinstance(item, dict) or not re.fullmatch(r"[a-z0-9_-]{1,60}", str(item.get("id") or "")):
                    raise MailServiceError(f"В {group_key} найдено правило без корректного id.")
                if item["id"] in ids:
                    raise MailServiceError(f"В {group_key} повторяется id {item['id']}.")
                ids.add(item["id"])
                if item.get("enabled") and group_key == "website_forms":
                    match = item.get("match") or {}
                    # A broad recipient-only form rule can classify ordinary correspondence.
                    if not (match.get("from_addresses") or match.get("subject_prefixes")):
                        raise MailServiceError(f"Форма {item['id']}: для включения укажите отправителя или начало темы.")
        importance_rules = rules.setdefault("importance_rules", [])
        if not isinstance(importance_rules, list):
            raise MailServiceError("importance_rules должен быть списком.")
        rule_ids = set()
        for item in importance_rules:
            if not isinstance(item, dict) or not re.fullmatch(r"[a-z0-9_-]{1,60}", str(item.get("id") or "")):
                raise MailServiceError("В importance_rules найдено правило без корректного id.")
            if item["id"] in rule_ids:
                raise MailServiceError(f"В importance_rules повторяется id {item['id']}.")
            rule_ids.add(item["id"])
            sender = str(item.get("sender_address") or "").strip().lower()
            recipient = str(item.get("recipient_address") or "").strip().lower()
            subject = str(item.get("subject") or "").strip()
            if sum(bool(x) for x in (sender, recipient, subject)) != 1:
                raise MailServiceError(f"Правило {item['id']}: задайте ровно один критерий — отправитель, адресат или тема.")
            for label, address in (("отправителя", sender), ("адресата", recipient)):
                if address and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
                    raise MailServiceError(f"Правило {item['id']}: неверный адрес {label}.")
            if subject and len(subject) > 200:
                raise MailServiceError(f"Правило {item['id']}: тема или фраза не должна быть длиннее 200 символов.")
            subject_match = item.get("subject_match", "contains")
            if subject and subject_match not in ("exact", "contains"):
                raise MailServiceError(f"Правило {item['id']}: subject_match должен быть exact или contains.")
            if item.get("importance", "important") not in ("important", "normal"):
                raise MailServiceError(f"Правило {item['id']}: importance должен быть important или normal.")
            item["sender_address"] = sender
            item["recipient_address"] = recipient
            item["subject"] = subject
            item["subject_match"] = subject_match
            item["importance"] = item.get("importance", "important")
            item["enabled"] = bool(item.get("enabled", True))
            item["name"] = str(item.get("name") or "")[:80]
        prefs = rules.get("reply_preferences") or {}
        self_addresses = prefs.get("confirmed_self_addresses", [])
        if not isinstance(self_addresses, list) or any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(x)) for x in self_addresses):
            raise MailServiceError("confirmed_self_addresses содержит неверный адрес.")
        return rules

    def _default_rules(self):
        path = DATA_DIR / "mail-rules.default.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"schema_version": 2, "accounts": [], "important_contacts": [], "important_recipient_groups": [], "importance_rules": [],
                "website_forms": [], "exclusions": {"skip_from_self": True, "skip_sent_by_dewiar": True, "test_markers": ["Test", "Codex"]},
                "reply_preferences": {"mode": "reply_all", "confirmed_self_addresses": [], "reply_from": "received_address",
                                      "quote_original": True, "include_own_address": True},
                "signature_by_account": {}, "sync": {"interval_minutes": 5, "initial_days": 7},
                "privacy": {"allow_cloud_model": True}}

    def _load_rules(self):
        try:
            rules = json.loads(self.rules_path.read_text(encoding="utf-8")) if self.rules_path.exists() else self._default_rules()
            return self.validate_rules(rules)
        except (OSError, ValueError, TypeError) as exc:
            self._rules_error = str(exc)
            log.error("Настройки почты не применены, используется безопасный набор по умолчанию: %s", exc)
            return self.validate_rules(self._default_rules())

    def rules_public(self):
        return {"rules": self.rules, "error": self._rules_error}

    def save_rules(self, rules):
        candidate = json.loads(json.dumps(rules))
        self.validate_rules(candidate)
        with self._lock:
            if self.rules_path.exists():
                backup = self.rules_path.with_name("mail-rules.previous.json")
                shutil.copy2(self.rules_path, backup)
            _atomic_json(self.rules_path, candidate)
            self.rules = candidate
            self._rules_error = ""
            self._load_accounts()
            self._reclassify()
        callback = getattr(self, "on_counts_change", None)
        if callback:
            try:
                callback(self.badge_counts())
            except Exception:
                log.exception("Не удалось обновить счётчик значка почты")
        return self.rules_public()

    def importance_rule_add(self, sender_address="", recipient_address="", subject="", subject_match="contains", importance="important", name=""):
        """Add or update one sender, recipient or subject rule and reclassify cached messages."""
        sender = str(sender_address or "").strip().lower()
        recipient = str(recipient_address or "").strip().lower()
        phrase = str(subject or "").strip()
        if sum(bool(x) for x in (sender, recipient, phrase)) != 1:
            raise MailServiceError("Укажите ровно одно: отправитель, адресат или тема письма.")
        for label, address in (("отправителя", sender), ("адресата", recipient)):
            if address and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
                raise MailServiceError("Неверный адрес " + label + ".")
        if phrase and len(phrase) > 200:
            raise MailServiceError("Тема или фраза не должна быть длиннее 200 символов.")
        if phrase and subject_match not in ("exact", "contains"):
            raise MailServiceError("Для темы выберите точное совпадение или вхождение фразы.")
        if importance not in ("important", "normal"):
            raise MailServiceError("Важность должна быть important или normal.")
        rules = json.loads(json.dumps(self.rules))
        items = rules.setdefault("importance_rules", [])
        match_key = ("sender", sender) if sender else ("recipient", recipient) if recipient else ("subject", phrase.casefold(), subject_match)
        def same_criterion(item):
            if sender:
                return (str(item.get("sender_address") or "").strip().lower(),) == match_key[1:]
            if recipient:
                return (str(item.get("recipient_address") or "").strip().lower(),) == match_key[1:]
            return (str(item.get("subject") or "").strip().casefold(), item.get("subject_match", "contains")) == match_key[1:]
        existing = next((item for item in items if same_criterion(item)), None)
        if existing:
            existing.update({"importance": importance, "enabled": True, "name": str(name or existing.get("name") or "")[:80]})
            rule = existing
        else:
            rule = {"id": "priority-" + uuid.uuid4().hex[:12], "name": str(name or "")[:80],
                    "sender_address": sender, "recipient_address": recipient, "subject": phrase,
                    "subject_match": subject_match if phrase else "contains", "importance": importance, "enabled": True}
            items.append(rule)
        self.save_rules(rules)
        return {"rule": rule, "rules": self.rules.get("importance_rules", [])}

    def importance_rule_remove(self, rule_id):
        rules = json.loads(json.dumps(self.rules))
        items = rules.setdefault("importance_rules", [])
        kept = [item for item in items if item.get("id") != str(rule_id)]
        if len(kept) == len(items):
            raise MailServiceError("Правило важности не найдено.")
        rules["importance_rules"] = kept
        self.save_rules(rules)
        return {"removed": str(rule_id), "rules": kept}

    # -- account setup ---------------------------------------------------------
    def _migrate_legacy(self):
        legacy = CONFIG_DIR / "mail.json"
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            address = str(data.get("address") or "").lower()
            account = next((x for x in self.rules.get("accounts", []) if x["address"] == address), None)
            if account:
                dst = self.account_dir / f"{account['id']}.json"
                # Let Mail perform the old plaintext-password -> Keychain migration before copying.
                legacy_box = Mail(legacy, self.workspace, migrate_keychain=False)
                if not dst.exists():
                    self.account_dir.mkdir(parents=True, exist_ok=True)
                    _atomic_json(dst, legacy_box.data)
                # Keep legacy source as a rollback copy until a successful account test.
        except (OSError, ValueError):
            log.exception("Не удалось прочитать прежние настройки почты")

    def _load_accounts(self):
        self.account_dir.mkdir(parents=True, exist_ok=True)
        self._accounts = {x["id"]: Mail(self.account_dir / f"{x['id']}.json", self.workspace,
                                        migrate_keychain=self._is_activated)
                          for x in self.rules.get("accounts", [])}
        for spec in self.rules.get("accounts", []):
            box = self._accounts[spec["id"]]
            defaults = guess(spec["address"])
            box.data.setdefault("address", spec["address"])
            box.data.setdefault("provider", defaults.get("provider", ""))
            for key in ("smtp_host", "smtp_port", "smtp_security", "imap_host", "imap_port"):
                if spec.get(key):
                    box.data.setdefault(key, spec[key])
                elif defaults.get(key):
                    box.data.setdefault(key, defaults[key])
            box.data["sent_copy"] = spec.get("sent_copy", "auto")

    def accounts(self):
        out = []
        for spec in self.rules.get("accounts", []):
            box = self._accounts.get(spec["id"])
            public = box.public() if box else {}
            out.append({"id": spec["id"], "address": spec["address"], "label": spec.get("label") or spec["address"],
                        "enabled": bool(spec.get("enabled")) and bool(public.get("configured")),
                        "configured": bool(public.get("configured")), "has_password": bool(public.get("has_password")),
                        "credential_state": public.get("credential_state", ""),
                        "needs_password": public.get("credential_state", "") in ATTENTION_STATES,
                        "smtp_host": public.get("smtp_host") or spec.get("smtp_host", ""), "smtp_port": public.get("smtp_port") or spec.get("smtp_port", ""),
                        "smtp_security": public.get("smtp_security", "ssl"), "imap_host": public.get("imap_host", ""),
                        "imap_port": public.get("imap_port") or spec.get("imap_port", ""), "login": public.get("login", ""),
                        "name": public.get("name", ""), "help": public.get("help", ""),
                        "last_checked": self._last_checked(spec["id"]), "last_error": self._last_error(spec["id"])})
        return out

    def account_get(self, account_id):
        if account_id not in self._accounts:
            raise MailServiceError("Такого почтового ящика нет в правилах.")
        return self._accounts[account_id]

    def account_add(self, address, label=""):
        """Add a mailbox without credentials; the UI collects its app password separately."""
        address = str(address or "").strip().lower()
        if len(address) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
            raise MailServiceError("Введите корректный адрес почты.")
        with self._lock:
            if any(item["address"] == address for item in self.rules.get("accounts", [])):
                raise MailServiceError("Этот адрес уже добавлен.")
            local = address.split("@", 1)[0].lower()
            slug = re.sub(r"[^a-z0-9_-]+", "-", local).strip("-_")[:24] or "mail"
            account_id = f"{slug}-{uuid.uuid4().hex[:8]}"
            candidate = json.loads(json.dumps(self.rules))
            candidate["accounts"].append({"id": account_id, "address": address,
                                          "label": str(label or "").strip()[:80] or address,
                                          "enabled": False, "sent_copy": "auto"})
            self.save_rules(candidate)
            return next(account for account in self.accounts() if account["id"] == account_id)

    def account_save(self, account_id, patch):
        box = self.account_get(account_id)
        spec = next(x for x in self.rules["accounts"] if x["id"] == account_id)
        if str((patch or {}).get("address") or spec["address"]).strip().lower() != spec["address"]:
            raise MailServiceError("Адрес аккаунта закреплён в правилах; выберите нужный ящик из списка.")
        patch = dict(patch or {})
        secret = str(patch.pop("password", "") or "")
        result = box.save({**patch, "address": spec["address"]})
        # Как в Apple Mail: новый пароль сначала проверяется на сервере. Не принят — прежний не затираем.
        test = box.test(candidate=secret or None)
        password_saved = bool(secret) and not test.get("auth_rejected")
        if password_saved:
            result = box.save({"password": secret, "address": spec["address"]})
        if test.get("smtp") == "ok" and test.get("imap") == "ok":
            candidate = json.loads(json.dumps(self.rules))
            next(x for x in candidate["accounts"] if x["id"] == account_id)["enabled"] = True
            self.save_rules(candidate)
            # The old config remains as a recoverable copy; don't silently delete it.
        self._notify_counts()
        return {"account": next(x for x in self.accounts() if x["id"] == account_id), "test": test,
                "saved": result.get("configured", False), "password_saved": password_saved}

    def account_unlock(self, account_id):
        """«Разрешить доступ к Связке ключей»: одно окно macOS по действию пользователя, затем проверка ящика."""
        box = self.account_get(account_id)
        box.unlock()
        test = box.test()
        self._notify_counts()
        return {"account": next(x for x in self.accounts() if x["id"] == account_id), "test": test}

    def _notify_counts(self):
        callback = getattr(self, "on_counts_change", None)
        if callback:
            try:
                callback(self.badge_counts())
            except Exception:
                log.exception("Не удалось обновить значок почты")

    def account_forget(self, account_id):
        box = self.account_get(account_id)
        result = box.forget()
        candidate = json.loads(json.dumps(self.rules))
        next(x for x in candidate["accounts"] if x["id"] == account_id)["enabled"] = False
        self.save_rules(candidate)
        return result

    def default_box(self):
        configured = [x for x in self.rules.get("accounts", []) if x.get("enabled") and self._accounts[x["id"]].configured()]
        if not configured:
            raise MailError("Сначала подключите хотя бы один ящик в Студия → Подключение → Почта.")
        return self._accounts[configured[0]["id"]]

    # Backward-compatible transport API for existing Dewiar tools.
    def configured(self):
        # Tool catalogs and the initial UI handshake query this before Mail opens.
        # accounts() calls Mail.public(), which may require an interactive Keychain grant.
        if not self._is_activated:
            return False
        return any(x["enabled"] for x in self.accounts())

    def public(self):
        return {"configured": self.configured(), "accounts": self.accounts()}

    def test(self):
        box = self.default_box()
        return box.test()

    def send_test(self, account_id):
        box = self.account_get(account_id)
        return box.send("", "Dewiar: проверка почты",
                        "Здравствуйте!\n\nЭто проверочное письмо от голосового ассистента Dewiar. "
                        "Подключение почты работает.\n\n— Dewiar")

    def save(self, patch):
        address = str((patch or {}).get("address") or "").lower()
        spec = next((x for x in self.rules.get("accounts", []) if x["address"] == address), None)
        if not spec:
            raise MailServiceError("Этот адрес не входит в список собственных ящиков почты.")
        return self.account_save(spec["id"], patch)

    def forget(self):
        boxes = [self.account_forget(a["id"]) for a in self.rules.get("accounts", []) if self._accounts[a["id"]].configured()]
        return {"configured": False, "removed": len(boxes)}

    def send(self, *args, **kwargs):
        return self.default_box().send(*args, **kwargs)

    def inbox(self, limit=10, unread=False, query="", folder="INBOX"):
        return self.list_mail(limit=limit, unread=unread, query=query)

    def mail_folders(self, account_id):
        box = self.account_get(account_id)
        try:
            folders = box.folders()
        except MailError as exc:
            raise MailServiceError(str(exc)) from exc
        result = []
        labels = {"INBOX": "Входящие", "DRAFTS": "Черновики", "SENT": "Отправленные",
                  "TRASH": "Удалённые", "JUNK": "Спам", "ARCHIVE": "Архив"}
        for folder in folders:
            result.append({"id": folder["id"], "name": labels.get(folder["id"], folder["name"]), "standard": folder["id"] in
                           {"INBOX", "DRAFTS", "SENT", "TRASH", "JUNK", "ARCHIVE"}})
            if folder["id"] == "SENT":
                result.append({"id": "REPLIES", "name": "Ответы", "standard": True, "virtual": True})
        return result

    def _mailbox_info(self, account_id, folder_id):
        box = self.account_get(account_id)
        if folder_id == "REPLIES":
            folder_id = "SENT"
        try:
            folder = next((item for item in box.folders() if item["id"] == folder_id), None)
        except MailError as exc:
            raise MailServiceError(str(exc)) from exc
        if not folder:
            raise MailServiceError("Эта папка больше не найдена на почтовом сервере. Обновите список папок.")
        return box, folder

    def sync_folder(self, account_id, folder_id):
        """Fetch one explicitly selected mailbox and index only its headers."""
        box, folder = self._mailbox_info(account_id, folder_id)
        stored_folder = folder["id"]
        try:
            rows = box.inbox(limit=500, folder=folder["mailbox"])
            uidvalidity = box.uidvalidity(folder["mailbox"])
        except MailError as exc:
            raise MailServiceError(str(exc)) from exc
        spec = next(item for item in self.rules.get("accounts", []) if item["id"] == account_id)
        with self._db() as db:
            for row in rows:
                self._store_message(db, spec, row, uidvalidity, stored_folder)
        return {"account_id": account_id, "folder_id": folder_id, "headers": len(rows)}

    def read(self, uid, folder="INBOX"):
        if isinstance(uid, str) and ":" in uid:
            parts = uid.split(":")
            account_id = parts[0]
            if len(parts) == 3:
                validity, uid = parts[1:]
                with self._db() as db:
                    known = db.execute("SELECT 1 FROM messages WHERE account_id=? AND folder=? AND uidvalidity=? AND uid=?",
                                       (account_id, folder, validity, uid)).fetchone()
                if not known:
                    raise MailServiceError("Письмо больше не находится в текущей версии папки.")
            else:
                uid = ":".join(parts[1:])
            box = self.account_get(account_id)
        else:
            box = self.default_box()
            account_id = box.data.get("account_id", "")
        result = box.read(uid, folder=folder)
        result["account_id"] = account_id
        return result

    # -- index -----------------------------------------------------------------
    def _connect(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.index_path, timeout=10)
        try:
            os.chmod(self.index_path, 0o600)
        except OSError:
            pass
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _db_init(self):
        with self._db() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS messages (
                account_id TEXT NOT NULL, folder TEXT NOT NULL, uidvalidity TEXT NOT NULL, uid TEXT NOT NULL,
                message_id TEXT, in_reply_to TEXT, refs TEXT, sender TEXT, sender_name TEXT, recipients TEXT,
                cc TEXT, subject TEXT, date TEXT, received_at REAL, delivered_to TEXT, importance TEXT,
                reasons TEXT NOT NULL DEFAULT '[]', auth_results TEXT NOT NULL DEFAULT '', auth_warning INTEGER NOT NULL DEFAULT 0,
                unread INTEGER NOT NULL DEFAULT 0,
                answered INTEGER NOT NULL DEFAULT 0, from_self INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL, PRIMARY KEY(account_id,folder,uidvalidity,uid));
              CREATE INDEX IF NOT EXISTS messages_date ON messages(received_at DESC);
              CREATE TABLE IF NOT EXISTS mailbox_state (account_id TEXT PRIMARY KEY, last_checked REAL, last_error TEXT, last_reported REAL);
              CREATE TABLE IF NOT EXISTS reply_drafts (id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                account_id TEXT NOT NULL, original_key TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, result TEXT);
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
            if "auth_results" not in columns:
                db.execute("ALTER TABLE messages ADD COLUMN auth_results TEXT NOT NULL DEFAULT ''")
            if "auth_warning" not in columns:
                db.execute("ALTER TABLE messages ADD COLUMN auth_warning INTEGER NOT NULL DEFAULT 0")

    def _last_checked(self, account_id):
        with self._db() as db:
            row = db.execute("SELECT last_checked FROM mailbox_state WHERE account_id=?", (account_id,)).fetchone()
            return row[0] if row else None

    def _last_error(self, account_id):
        with self._db() as db:
            row = db.execute("SELECT last_error FROM mailbox_state WHERE account_id=?", (account_id,)).fetchone()
            return row[0] if row else ""

    def sync_all(self, force=False):
        results, sent_ids = [], []
        active = [s for s in self.rules.get("accounts", []) if s.get("enabled") and self._accounts[s["id"]].configured()]
        for spec in active:
            aid, box = spec["id"], self._accounts[spec["id"]]
            try:
                previous = self._last_checked(aid)
                since_days = int((self.rules.get("sync") or {}).get("initial_days", 7)) if not previous else 2
                rows = box.inbox(limit=500, folder="INBOX", since_days=since_days)
                uidvalidity = box.uidvalidity("INBOX")
                with self._db() as db:
                    for row in rows:
                        self._store_message(db, spec, row, uidvalidity, "INBOX")
                    db.execute("INSERT INTO mailbox_state(account_id,last_checked,last_error) VALUES(?,?,?) "
                               "ON CONFLICT(account_id) DO UPDATE SET last_checked=excluded.last_checked,last_error=''",
                               (aid, time.time(), ""))
                results.append({"account_id": aid, "ok": True, "headers": len(rows)})
                sent_folder = box.special_folder("\\Sent")
                if sent_folder:
                    srows = box.inbox(limit=500, folder=sent_folder)
                    sent_uidvalidity = box.uidvalidity(sent_folder)
                    with self._db() as db:
                        for sr in srows:
                            self._store_message(db, spec, sr, sent_uidvalidity, "SENT")
                    for sr in srows:
                        if sr.get("message_id"):
                            sent_ids.append(sr["message_id"])
                        if sr.get("in_reply_to"):
                            sent_ids.append(sr["in_reply_to"])
                        sent_ids.extend(re.findall(r"<[^>]+>", sr.get("references", "")))
            except Exception as exc:  # per-account fault isolation
                log.warning("Почтовая синхронизация %s: %s", aid, exc)
                with self._db() as db:
                    db.execute("INSERT INTO mailbox_state(account_id,last_error) VALUES(?,?) "
                               "ON CONFLICT(account_id) DO UPDATE SET last_error=excluded.last_error", (aid, str(exc)[:500]))
                results.append({"account_id": aid, "ok": False, "error": str(exc)[:300]})
        if sent_ids:
            with self._db() as db:
                for message_id in set(sent_ids):
                    db.execute("UPDATE messages SET answered=1 WHERE message_id=? OR instr(refs,?)>0", (message_id, message_id))
        return {"accounts": results, "at": time.time()}

    def _store_message(self, db, spec, row, uidvalidity, folder):
        own = {x.lower() for x in (self.rules.get("reply_preferences") or {}).get("confirmed_self_addresses", [])}
        sender_addr = _addr(row.get("from"))
        recipients = _addresses(row.get("to"))
        cc = _addresses(row.get("cc"))
        delivered = _addresses(row.get("delivered_to")) + _addresses(row.get("x_original_to"))
        msg = {**row, "account_id": spec["id"], "received_address": spec["address"]}
        important, reasons = self.classify(msg)
        from_self = sender_addr in own
        reason_dump = json.dumps(reasons, ensure_ascii=False)
        auth_results = str(row.get("authentication_results") or "")[:4000]
        auth_warning = bool(re.search(r"(?:spf|dkim|dmarc)\s*=\s*fail\b", auth_results, re.I))
        if row.get("message_id"):
            old = db.execute("SELECT account_id,folder,uidvalidity,uid,answered FROM messages "
                             "WHERE account_id=? AND folder=? AND message_id=? LIMIT 1",
                             (spec["id"], folder, row["message_id"])).fetchone()
            if old and (old["uidvalidity"] != str(uidvalidity) or old["uid"] != str(row.get("uid"))):
                db.execute("DELETE FROM messages WHERE account_id=? AND folder=? AND uidvalidity=? AND uid=?",
                           (old["account_id"], old["folder"], old["uidvalidity"], old["uid"]))
        db.execute("""INSERT INTO messages(account_id,folder,uidvalidity,uid,message_id,in_reply_to,refs,sender,sender_name,
                  recipients,cc,subject,date,received_at,delivered_to,importance,reasons,auth_results,auth_warning,unread,answered,from_self,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(account_id,folder,uidvalidity,uid) DO UPDATE SET message_id=excluded.message_id,
                  in_reply_to=excluded.in_reply_to,refs=excluded.refs,sender=excluded.sender,sender_name=excluded.sender_name,
                  recipients=excluded.recipients,cc=excluded.cc,subject=excluded.subject,date=excluded.date,
                  received_at=excluded.received_at,delivered_to=excluded.delivered_to,importance=excluded.importance,
                  reasons=excluded.reasons,auth_results=excluded.auth_results,auth_warning=excluded.auth_warning,
                  unread=excluded.unread,answered=MAX(messages.answered,excluded.answered),
                  from_self=excluded.from_self,updated_at=excluded.updated_at""",
                  (spec["id"], folder, str(uidvalidity), str(row.get("uid")), row.get("message_id", ""), row.get("in_reply_to", ""),
                   row.get("references", ""), sender_addr, row.get("from", ""), json.dumps(recipients), json.dumps(cc),
                   row.get("subject", ""), row.get("date", ""),
                   float(row["received_at"]) if row.get("received_at") is not None else _stamp(row.get("date", "")), json.dumps(delivered),
                   "important" if important else "normal", reason_dump, auth_results, int(auth_warning), int(row.get("unread", False)), int(row.get("answered", False)),
                   int(from_self), time.time()))

    def classify(self, msg):
        """Rules are explicit, deterministic and explainable. Match only parsed header addresses."""
        sender = _addr(msg.get("from"))
        to = set(_addresses(msg.get("to")))
        cc = set(_addresses(msg.get("cc")))
        delivered = set(_addresses(msg.get("delivered_to"))) | set(_addresses(msg.get("x_original_to")))
        own = {x.lower() for x in (self.rules.get("reply_preferences") or {}).get("confirmed_self_addresses", [])}
        if (self.rules.get("exclusions") or {}).get("skip_from_self", True) and sender in own:
            return False, ["Собственное письмо — учитывается в цепочке, но не считается новым важным"]
        subject = str(msg.get("subject") or "").strip().casefold()
        for rule in reversed(self.rules.get("importance_rules", [])):
            if not rule.get("enabled", True):
                continue
            address = str(rule.get("sender_address") or "").strip().lower()
            recipient = str(rule.get("recipient_address") or "").strip().lower()
            phrase = str(rule.get("subject") or "").strip().casefold()
            if address:
                matched = sender == address
            elif recipient:
                targets = set(_addresses(msg.get("to"))) | set(_addresses(msg.get("cc")))
                targets |= set(_addresses(msg.get("delivered_to"))) | set(_addresses(msg.get("x_original_to")))
                matched = recipient in targets
            else:
                matched = subject == phrase if rule.get("subject_match", "contains") == "exact" else phrase in subject
            if matched:
                detail = ("отправитель " + address) if address else ("адресат " + recipient) if recipient else (
                    "тема точно «" + str(rule["subject"]) + "»" if rule.get("subject_match") == "exact" else
                    "тема содержит «" + str(rule["subject"]) + "»")
                label = rule.get("name") or detail
                return rule.get("importance", "important") == "important", [
                    ("Важно: " if rule.get("importance", "important") == "important" else "Обычная важность: ") + label]
        reasons = []
        for group in self.rules.get("important_recipient_groups", []):
            if not group.get("enabled", True):
                continue
            targets = {str(x).lower() for x in group.get("addresses", [])}
            if (msg.get("account_id") in group.get("account_ids", []) or
                    targets & (to | cc | delivered) or (msg.get("received_address", "").lower() in targets)):
                reasons.append(group.get("name") or "Важный получатель")
        for contact in self.rules.get("important_contacts", []):
            if contact.get("enabled", True) and sender in {str(x).lower() for x in contact.get("addresses", [])}:
                reasons.append("От " + (contact.get("name") or sender))
        for form in self.rules.get("website_forms", []):
            if not form.get("enabled"):
                continue
            match = form.get("match") or {}
            from_ok = not match.get("from_addresses") or sender in {x.lower() for x in match["from_addresses"]}
            recipient_ok = not match.get("recipient_any") or bool({x.lower() for x in match["recipient_any"]} & (to | cc | delivered))
            subject = str(msg.get("subject") or "").casefold()
            prefixes = [str(x).casefold() for x in match.get("subject_prefixes", [])]
            subject_ok = not prefixes or any(subject.startswith(x) for x in prefixes)
            reply_ok = not match.get("reply_to_required") or bool(_addr(msg.get("reply_to")))
            # Require multiple independent clues; empty conditions can never mean match-all.
            if from_ok and recipient_ok and subject_ok and reply_ok and (match.get("from_addresses") or prefixes):
                reasons.append("Заявка с сайта " + str(form.get("site") or ""))
        return bool(reasons), list(dict.fromkeys(reasons))

    def _reclassify(self):
        with self._db() as db:
            rows = db.execute("SELECT * FROM messages").fetchall()
            for row in rows:
                spec = next((x for x in self.rules.get("accounts", []) if x["id"] == row["account_id"]), {})
                msg = {"from": row["sender_name"], "to": ",".join(json.loads(row["recipients"] or "[]")),
                      "cc": ",".join(json.loads(row["cc"] or "[]")), "delivered_to": ",".join(json.loads(row["delivered_to"] or "[]")),
                      "account_id": row["account_id"], "received_address": spec.get("address", ""), "subject": row["subject"]}
                important, reasons = self.classify(msg)
                db.execute("UPDATE messages SET importance=?,reasons=?,updated_at=? WHERE account_id=? AND folder=? AND uidvalidity=? AND uid=?",
                           ("important" if important else "normal", json.dumps(reasons, ensure_ascii=False), time.time(),
                            row["account_id"], row["folder"], row["uidvalidity"], row["uid"]))

    def start_background(self, stop_event):
        interval = max(180, int((self.rules.get("sync") or {}).get("interval_minutes", 5)) * 60)
        # Как Apple Mail: почта проверяется с запуска, без открытия студии. Пароли читаются без окон macOS
        # (mail_keychain): если доступа нет, ящик помечается «нужен пароль», а запуск ничем не блокируется.
        if not stop_event.wait(STARTUP_SYNC_DELAY):
            try:
                self.activate()
            except Exception:
                log.exception("Не удалось включить почту при запуске")
        while not stop_event.is_set():
            if not self._activated.wait(0.5):
                continue
            try:
                self.sync_all()
            except Exception:
                log.exception("Почтовая синхронизация завершилась ошибкой")
            finally:
                callback = getattr(self, "on_counts_change", None)
                if callback:
                    try:
                        callback(self.badge_counts())
                    except Exception:
                        log.exception("Не удалось обновить счётчик непрочитанных писем")
            stop_event.wait(interval)

    def unread_count(self):
        """Count cached unread inbox messages, deduplicating copies in several accounts."""
        with self._db() as db:
            row = db.execute("SELECT COUNT(DISTINCT COALESCE(NULLIF(message_id,''), "
                "account_id || ':' || uidvalidity || ':' || uid)) "
                "FROM messages WHERE folder='INBOX' AND unread=1").fetchone()
        return int(row[0] or 0)

    def important_count(self):
        """Count cached important inbox messages, deduplicating copies in several accounts."""
        with self._db() as db:
            row = db.execute("SELECT COUNT(DISTINCT COALESCE(NULLIF(message_id,''), "
                "account_id || ':' || uidvalidity || ':' || uid)) "
                "FROM messages WHERE folder='INBOX' AND importance='important' AND from_self=0").fetchone()
        return int(row[0] or 0)

    def badge_count(self, filters=None):
        """Count incoming messages matching all selected badge filters."""
        if filters is None:
            provider = getattr(self, "badge_filters_provider", None)
            filters = provider() if callable(provider) else {}
        filters = filters if isinstance(filters, dict) else {}
        clauses = ["folder='INBOX'", "from_self=0"]
        if filters.get("important"):
            clauses.append("importance='important'")
        if filters.get("unread"):
            clauses.append("unread=1")
        if filters.get("unanswered"):
            clauses.append("answered=0")
        with self._db() as db:
            row = db.execute("SELECT COUNT(DISTINCT COALESCE(NULLIF(message_id,''), "
                "account_id || ':' || uidvalidity || ':' || uid)) "
                "FROM messages WHERE " + " AND ".join(clauses)).fetchone()
        return int(row[0] or 0)

    def attention_count(self):
        """Сколько ящиков ждут пароль. Связку ключей не читает: только то, что выяснилось при входе на сервер."""
        enabled = [x["id"] for x in self.rules.get("accounts", []) if x.get("enabled")]
        return sum(1 for aid in enabled if getattr(self._accounts.get(aid), "_secret_state", "") in ATTENTION_STATES)

    def badge_counts(self):
        provider = getattr(self, "badge_filters_provider", None)
        filters = provider() if callable(provider) else {}
        filters = filters if isinstance(filters, dict) else {}
        return {"unread": self.unread_count(), "important": self.important_count(),
                "badge": self.badge_count(filters), "filters": filters,
                "attention": self.attention_count()}

    def check(self, force=False):
        if force:
            self.sync_all(force=True)
        accounts = self.accounts()
        with self._db() as db:
            rows = db.execute("SELECT * FROM messages WHERE folder='INBOX' ORDER BY received_at DESC LIMIT 1000").fetchall()
            states = {r["account_id"]: r for r in db.execute("SELECT * FROM mailbox_state")}
            count_new = 0
            important = []
            for row in rows:
                state = states.get(row["account_id"])
                if state and row["received_at"] > (state["last_reported"] or 0) and not row["from_self"]:
                    count_new += 1
                if row["importance"] == "important" and not row["answered"] and not row["from_self"]:
                    important.append(self._public_row(row))
            now = time.time()
            for acc in accounts:
                db.execute("INSERT INTO mailbox_state(account_id,last_reported) VALUES(?,?) "
                           "ON CONFLICT(account_id) DO UPDATE SET last_reported=excluded.last_reported",
                           (acc["id"], now))
        return {"new": count_new, "important": important[:50], "accounts": accounts,
                "unavailable": [{"id": a["id"], "address": a["address"], "error": a["last_error"] or "Ящик не подключён"}
                                for a in accounts if not a["enabled"] or a["last_error"]]}

    @staticmethod
    def _thread_key(row):
        return row["message_id"] or f"{row['account_id']}:{row['folder']}:{row['uidvalidity']}:{row['uid']}"

    @staticmethod
    def _base_subject(subject):
        value = str(subject or "").strip().casefold()
        previous = None
        while value != previous:
            previous = value
            value = re.sub(r"^\s*(?:(?:re|fw|fwd)(?:\s*\[\d+\])?\s*:\s*)+", "", value, flags=re.I)
            value = re.sub(r"^\s*\[fwd:\s*|\s*\(fwd\)\s*$", "", value, flags=re.I).strip()
        return " ".join(value.split())

    @staticmethod
    def _is_reply_subject(subject):
        return bool(re.match(r"^\s*(?:(?:re|fw|fwd)(?:\s*\[\d+\])?\s*:\s*)+", str(subject or ""), re.I))

    def _thread_groups(self, rows):
        """Build conservative RFC-style conversation groups from Message-ID ancestry.

        References is preferred, then In-Reply-To. When a client strips both headers,
        a matching base subject is used only when at least one message is marked Re/Fw.
        """
        rows = list(rows)
        parents, row_roots = {}, {}
        for row in rows:
            mid = row["message_id"]
            if not mid:
                continue
            refs = re.findall(r"<[^<>\s]+>", str(row["refs"] or ""))
            if not refs:
                refs = re.findall(r"<[^<>\s]+>", str(row["in_reply_to"] or ""))[:1]
            if refs:
                for parent, child in zip(refs, refs[1:]):
                    parents.setdefault(child, parent)
                parents[mid] = refs[-1]

        def root(mid):
            current, visited = mid, set()
            while current in parents and current not in visited:
                visited.add(current)
                current = parents[current]
            return current

        roots_by_subject = {}
        for row in rows:
            key = self._thread_key(row)
            mid = row["message_id"] or key
            row_roots[key] = root(mid)
            base = self._base_subject(row["subject"])
            if base:
                item = roots_by_subject.setdefault(base, {})
                entry = item.setdefault(row_roots[key], {"reply": False})
                entry["reply"] = entry["reply"] or self._is_reply_subject(row["subject"])

        # RFC 5256's REFERENCES algorithm has a base-subject fallback. Requiring an
        # explicit reply/forward marker avoids folding unrelated same-title mail.
        aliases = {}
        for subject_roots in roots_by_subject.values():
            roots = list(subject_roots)
            if len(roots) > 1 and any(info["reply"] for info in subject_roots.values()):
                canonical = roots[0]
                for candidate in roots[1:]:
                    aliases[candidate] = canonical

        def canonical(value):
            visited = set()
            while value in aliases and value not in visited:
                visited.add(value); value = aliases[value]
            return value

        groups = {}
        for row in rows:
            key = self._thread_key(row)
            row_roots[key] = canonical(row_roots[key])
            groups.setdefault(row_roots[key], []).append(row)
        return groups, row_roots

    def _unique_thread_messages(self, rows, rank):
        unique, order = {}, []
        for row in rows:
            key = self._thread_key(row)
            public = self._public_row(row)
            message_key = row["message_id"] or key
            if message_key not in unique:
                unique[message_key] = (row, public); order.append(message_key)
                continue
            old_row, old = unique[message_key]
            deliveries = old.setdefault("received_on", [old["account"]])
            if public["account"] not in deliveries:
                deliveries.append(public["account"])
            if rank.get(public["account_id"], 10_000) < rank.get(old["account_id"], 10_000):
                public["received_on"] = deliveries
                unique[message_key] = (row, public)
        result = [unique[key] for key in order]
        result.sort(key=lambda item: item[0]["received_at"], reverse=True)
        return result

    def list_mail(self, limit=30, unread=False, query="", important=False, account_id="", unanswered=False, incoming_only=False, folder_id="INBOX"):
        if folder_id == "REPLIES":
            clauses, args = ["folder='SENT'", "in_reply_to!=''"], []
        else:
            clauses, args = ["folder=?"], [folder_id or "INBOX"]
        if incoming_only:
            clauses.append("from_self=0")
        if unread:
            clauses.append("unread=1")
        if important:
            clauses.append("importance='important'")
        if unanswered:
            clauses.append("answered=0 AND from_self=0")
        if account_id:
            clauses.append("account_id=?"); args.append(account_id)
        if query:
            clauses.append("(sender_name LIKE ? OR subject LIKE ? OR recipients LIKE ? OR cc LIKE ?)")
            args.extend([f"%{query}%"] * 4)
        with self._db() as db:
            matching = db.execute("SELECT * FROM messages WHERE " + " AND ".join(clauses) + " ORDER BY received_at DESC",
                                  args).fetchall()
            all_rows = db.execute("SELECT * FROM messages ORDER BY received_at DESC").fetchall()
        priority = (self.rules.get("reply_preferences") or {}).get("reply_from_priority", [])
        rank = {account_id: i for i, account_id in enumerate(priority)}
        groups, row_roots = self._thread_groups(all_rows)
        matching_roots = {row_roots[self._thread_key(row)] for row in matching}
        conversations = []
        for thread_id, rows in groups.items():
            if thread_id not in matching_roots:
                continue
            messages = self._unique_thread_messages(rows, rank)
            if not messages:
                continue
            latest_row, latest = messages[0]
            latest.update(thread_id=thread_id, thread_count=len(messages),
                          thread_unread_count=sum(bool(item[1]["unread"]) for item in messages),
                          thread_important=any(item[1]["importance"] == "important" for item in messages),
                          thread_answered=any(item[1]["answered"] for item in messages))
            deliveries = latest.setdefault("received_on", [latest["account"]])
            for _, public in messages[1:]:
                for address in public.get("received_on", [public["account"]]):
                    if address not in deliveries:
                        deliveries.append(address)
            conversations.append((latest_row["received_at"], latest))
        conversations.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in conversations[:max(1, min(200, int(limit)))]]

    def mail_thread(self, thread_id):
        with self._db() as db:
            rows = db.execute("SELECT * FROM messages ORDER BY received_at DESC").fetchall()
        groups, _ = self._thread_groups(rows)
        messages = groups.get(str(thread_id))
        if not messages:
            raise MailServiceError("Переписка не найдена в локальном индексе. Обновите входящие.")
        priority = (self.rules.get("reply_preferences") or {}).get("reply_from_priority", [])
        rank = {account_id: i for i, account_id in enumerate(priority)}
        return [item[1] for item in self._unique_thread_messages(messages, rank)]

    def thread_id_for_message(self, message_key):
        account_id, validity, uid, folder = self._parse_message_key(message_key)
        with self._db() as db:
            rows = db.execute("SELECT * FROM messages ORDER BY received_at DESC").fetchall()
        _, row_roots = self._thread_groups(rows)
        target = next((row for row in rows if row["account_id"] == account_id and row["folder"] == folder
                       and row["uidvalidity"] == validity and row["uid"] == uid), None)
        if not target:
            raise MailServiceError("Письмо отсутствует в локальном индексе.")
        return row_roots[self._thread_key(target)]

    def _public_row(self, row):
        folder = row["folder"]
        token = base64.urlsafe_b64encode(folder.encode("utf-8")).decode("ascii").rstrip("=")
        suffix = "" if folder == "INBOX" else ":s" if folder == "SENT" else ":f" + token
        return {"id": f"{row['account_id']}:{row['uidvalidity']}:{row['uid']}{suffix}", "uid": row["uid"], "uidvalidity": row["uidvalidity"], "account_id": row["account_id"], "folder": row["folder"],
                "account": next((a["address"] for a in self.rules.get("accounts", []) if a["id"] == row["account_id"]), ""),
                "from": row["sender_name"], "sender": row["sender"], "to": ", ".join(json.loads(row["recipients"] or "[]")),
                "cc": ", ".join(json.loads(row["cc"] or "[]")), "subject": row["subject"], "date": row["date"],
                "importance": row["importance"], "reasons": json.loads(row["reasons"] or "[]"), "unread": bool(row["unread"]),
                "answered": bool(row["answered"]), "from_self": bool(row["from_self"]), "message_id": row["message_id"],
                "auth_warning": bool(row["auth_warning"]), "authentication_results": row["auth_results"]}

    def open_mail(self, message_key):
        account_id, validity, uid, folder = self._parse_message_key(message_key)
        with self._db() as db:
            known = db.execute("SELECT folder FROM messages WHERE account_id=? AND folder=? AND uidvalidity=? AND uid=? LIMIT 1",
                               (account_id, folder, validity, uid)).fetchone()
        if not known:
            raise MailServiceError("Письмо отсутствует в локальном индексе. Обновите переписку.")
        box, folder_info = self._mailbox_info(account_id, folder)
        mailbox = folder_info["mailbox"]
        if str(box.uidvalidity(mailbox)) != validity:
            raise MailServiceError("Папка с письмом изменилась. Обновите переписку, чтобы открыть актуальное письмо.")
        detail = box.read(uid, folder=mailbox, expected_uidvalidity=validity)
        detail.update(account_id=account_id, id=message_key,
                      account=next((a["address"] for a in self.rules["accounts"] if a["id"] == account_id), ""), folder=folder)
        return detail

    def delete_mail(self, message_key):
        """Move one selected inbox copy to the server Trash; leave other account copies untouched."""
        account_id, validity, uid, folder = self._parse_message_key(message_key)
        if folder == "TRASH":
            raise MailServiceError("Письмо уже в папке «Удалённые».")
        with self._db() as db:
            known = db.execute("SELECT 1 FROM messages WHERE account_id=? AND folder=? AND uidvalidity=? AND uid=?",
                               (account_id, folder, validity, uid)).fetchone()
        if not known:
            raise MailServiceError("Письмо уже удалено или отсутствует в текущей версии папки.")
        box = self.account_get(account_id)
        _, folder_info = self._mailbox_info(account_id, folder)
        mailbox = folder_info["mailbox"]
        if str(box.uidvalidity(mailbox)) != validity:
            raise MailServiceError("Папка с письмом изменилась. Обновите список; удаление остановлено.")
        try:
            result = box.move_to_trash(uid, folder=mailbox, expected_uidvalidity=validity)
        except MailError as exc:
            raise MailServiceError(str(exc)) from exc
        with self._db() as db:
            db.execute("DELETE FROM messages WHERE account_id=? AND folder=? AND uidvalidity=? AND uid=?",
                       (account_id, folder, validity, uid))
        self._notify_counts()
        return {"state": result["state"], "folder": result.get("folder", ""), "id": message_key}

    @staticmethod
    def _parse_message_key(message_key):
        if not isinstance(message_key, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}:[0-9]+:[0-9]+(?::(?:s|f[A-Za-z0-9_-]+))?", message_key):
            raise MailServiceError("Письмо выбирается по id из списка.")
        parts = message_key.split(":")
        if len(parts) == 3:
            folder = "INBOX"
        elif parts[3] == "s":
            folder = "SENT"
        else:
            token = parts[3][1:]
            try:
                folder = base64.urlsafe_b64decode(token + "=" * ((4 - len(token) % 4) % 4)).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise MailServiceError("Папка письма указана неверно.") from exc
            if folder in ("INBOX", "SENT"):
                raise MailServiceError("Папка письма указана неверно.")
        return (parts[0], parts[1], parts[2], folder)

    @staticmethod
    def _draft_view(draft_id, revision, state, payload):
        public = {k: v for k, v in payload.items() if k != "forward_eml_b64"}
        public["forwarded_original"] = bool(payload.get("forward_eml_b64") or payload.get("forwarded_original"))
        return {"id": draft_id, "revision": revision, "state": state, **public}

    @staticmethod
    def _clear_forward_blob(payload):
        if payload.pop("forward_eml_b64", None):
            payload["forwarded_original"] = True
        return payload

    # -- confirmed reply-all drafts -------------------------------------------
    def reply_prepare(self, message_key, body):
        original = self.open_mail(message_key)
        account_id = original["account_id"]
        box = self.account_get(account_id)
        own = {str(x).lower() for x in (self.rules.get("reply_preferences") or {}).get("confirmed_self_addresses", [])}
        author = _addr(original.get("reply_to")) or _addr(original.get("from"))
        if not author or author in own:
            raise MailServiceError("У письма нет допустимого внешнего адреса автора для ответа.")
        to = [author]
        cc = []
        for address in _addresses(original.get("to")) + _addresses(original.get("cc")):
            if address not in own and address != author and address not in to and address not in cc:
                cc.append(address)
        if (self.rules.get("reply_preferences") or {}).get("include_own_address", True):
            mine = box.data.get("address", "").lower()
            if mine and mine not in to and mine not in cc:
                cc.append(mine)
        subject = str(original.get("subject") or "").strip()
        subject = subject if re.match(r"^\s*re\s*:", subject, re.I) else "Re: " + (subject or "(без темы)")
        quote = "\n".join("> " + line for line in (original.get("text") or "").splitlines())
        quote_date = _message_date_for_quote(original.get("date"), original.get("received_at"))
        quoted = f"\n\n{quote_date} · {original.get('from','')} написал(а):\n{quote}" if quote else ""
        signature = (self.rules.get("signature_by_account") or {}).get(account_id, "")
        text = (body or "").strip()
        if signature:
            text += "\n\n" + signature.strip()
        text += quoted
        payload = {"kind": "reply", "from": box.data.get("address", ""), "to": to, "cc": cc, "subject": subject, "body": text,
                   "in_reply_to": original.get("message_id", ""), "references": " ".join(x for x in
                     (original.get("references", ""), original.get("message_id", "")) if x), "attachments": [],
                   "original": {"from": original.get("from"), "to": original.get("to"), "cc": original.get("cc"),
                                "subject": original.get("subject"), "date": original.get("date"), "received_at": original.get("received_at"), "text": original.get("text"),
                                "files": original.get("files", [])}}
        draft_id, now = uuid.uuid4().hex, time.time()
        with self._db() as db:
            db.execute("INSERT INTO reply_drafts(id,revision,account_id,original_key,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (draft_id, 1, account_id, message_key, "draft", json.dumps(payload, ensure_ascii=False), now, now))
        return self._draft_view(draft_id, 1, "draft", payload)

    def forward_prepare(self, message_key):
        original = self.open_mail(message_key)
        account_id = original["account_id"]
        box = self.account_get(account_id)
        try:
            _, validity, _, folder = self._parse_message_key(message_key)
            _, folder_info = self._mailbox_info(account_id, folder)
            raw = box.read_raw(original["uid"], folder=folder_info["mailbox"], expected_uidvalidity=validity)
        except MailError as exc:
            raise MailServiceError(str(exc)) from exc
        subject = str(original.get("subject") or "(без темы)").strip()
        subject = subject if re.match(r"^\s*fwd?\s*:", subject, re.I) else "Fwd: " + subject
        details = ["---------- Пересланное сообщение ----------",
                   "От: " + str(original.get("from") or "—"),
                   "Дата: " + (_message_date_for_quote(original.get("date"), original.get("received_at")) or "—"),
                   "Тема: " + str(original.get("subject") or "(без темы)"),
                   "Кому: " + str(original.get("to") or "—")]
        if original.get("cc"):
            details.append("Копия: " + original["cc"])
        forwarded_text = "\n".join(details) + "\n\n" + (original.get("text") or "")
        signature = (self.rules.get("signature_by_account") or {}).get(account_id, "")
        body = (signature.strip() + "\n\n" if signature.strip() else "") + forwarded_text
        payload = {"kind": "forward", "from": box.data.get("address", ""), "to": [], "cc": [],
                   "subject": subject, "body": body, "in_reply_to": "", "references": "", "attachments": [],
                   "forward_eml_b64": base64.b64encode(raw).decode("ascii"),
                   "forward_filename": re.sub(r'[\\/:*?"<>|\r\n]+', "_", subject)[:110] + ".eml",
                   "original": {"from": original.get("from"), "to": original.get("to"), "cc": original.get("cc"),
                                "subject": original.get("subject"), "date": original.get("date"), "received_at": original.get("received_at"),
                                "text": original.get("text"), "files": original.get("files", [])}}
        draft_id, now = uuid.uuid4().hex, time.time()
        with self._db() as db:
            db.execute("INSERT INTO reply_drafts(id,revision,account_id,original_key,state,payload,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?)",
                       (draft_id, 1, account_id, message_key, "draft", json.dumps(payload, ensure_ascii=False), now, now))
        return self._draft_view(draft_id, 1, "draft", payload)

    def reply_revise(self, draft_id, revision, patch):
        with self._db() as db:
            row = db.execute("SELECT * FROM reply_drafts WHERE id=?", (draft_id,)).fetchone()
            if not row or row["state"] != "draft" or int(row["revision"]) != int(revision):
                raise MailServiceError("Черновик уже изменён или закрыт. Откройте актуальную версию и проверьте её.")
            payload = json.loads(row["payload"])
            if payload.get("kind") != "forward" and any(key in patch for key in ("to", "cc", "subject")):
                raise MailServiceError("Адресатов и тему ответа нельзя изменить в этом черновике.")
            if "body" in patch:
                payload["body"] = str(patch["body"] or "")[:30000]
            if "to" in patch:
                payload["to"] = _draft_addresses(patch["to"], "Кому")
            if "cc" in patch:
                payload["cc"] = _draft_addresses(patch["cc"], "Копия")
            if "subject" in patch:
                subject = str(patch["subject"] or "").strip()
                if not subject or len(subject) > 200:
                    raise MailServiceError("Тема письма должна содержать от 1 до 200 символов.")
                payload["subject"] = subject
            if "from" in patch and str(patch["from"]).lower() != payload["from"].lower():
                chosen = next((s for s in self.rules["accounts"] if s["address"] == str(patch["from"]).lower()), None)
                if not chosen or not self._accounts[chosen["id"]].configured():
                    raise MailServiceError("Этот адрес не подключён для отправки.")
                payload["from"] = chosen["address"]
            new_revision = int(row["revision"]) + 1
            db.execute("UPDATE reply_drafts SET revision=?,payload=?,updated_at=? WHERE id=? AND revision=?",
                       (new_revision, json.dumps(payload, ensure_ascii=False), time.time(), draft_id, int(revision)))
        return self._draft_view(draft_id, new_revision, "draft", payload)

    def reply_confirmation(self, draft_id, revision):
        with self._db() as db:
            row = db.execute("SELECT * FROM reply_drafts WHERE id=?", (draft_id,)).fetchone()
        if not row or row["state"] != "draft" or int(row["revision"]) != int(revision):
            raise MailServiceError("Эта версия черновика уже неактуальна. Откройте черновик снова.")
        p = json.loads(row["payload"])
        if not p.get("to"):
            raise MailServiceError("Укажите хотя бы одного получателя, прежде чем подтверждать отправку.")
        action = "пересылку" if p.get("kind") == "forward" else "ответ"
        return (f"Отправить {action} с {p['from']}? Кому: {', '.join(p['to']) or '—'}; "
                f"копия: {', '.join(p['cc']) or '—'}; тема: «{p['subject']}». "
                f"Версия черновика {revision}. Будет отправлен именно этот текст:\n\n{p['body']}")

    def reply_send(self, draft_id, revision):
        # Claim once before SMTP: an interrupted connection cannot trigger a blind duplicate retry.
        with self._db() as db:
            row = db.execute("SELECT * FROM reply_drafts WHERE id=?", (draft_id,)).fetchone()
            if not row or row["state"] != "draft" or int(row["revision"]) != int(revision):
                raise MailServiceError("Отправка отменена: подтверждённая версия уже изменена или использована.")
            payload = json.loads(row["payload"])
            account_id = row["account_id"]
            if not payload.get("to"):
                raise MailServiceError("Укажите хотя бы одного получателя перед отправкой.")
        original_id = payload.get("in_reply_to")
        if original_id:
            try:
                latest = self.account_get(account_id).inbox(limit=500, folder="INBOX", since_days=7)
            except Exception as exc:
                raise MailServiceError("Не удалось проверить, не пришло ли новое письмо в эту цепочку. Отправка остановлена; повторите проверку позже.") from exc
            original_time = _stamp(payload.get("original", {}).get("date", ""))
            for message in latest:
                linked = original_id in (message.get("in_reply_to", "") + " " + message.get("references", ""))
                if linked and _stamp(message.get("date", "")) > original_time:
                    raise MailServiceError("В этой переписке появилось новое письмо. Откройте его и подготовьте актуальный ответ заново.")
        with self._db() as db:
            row = db.execute("SELECT * FROM reply_drafts WHERE id=?", (draft_id,)).fetchone()
            if not row or row["state"] != "draft" or int(row["revision"]) != int(revision):
                raise MailServiceError("Отправка отменена: подтверждённая версия уже изменена или использована.")
            changed = db.execute("UPDATE reply_drafts SET state='sending',updated_at=? WHERE id=? AND revision=? AND state='draft'",
                                 (time.time(), draft_id, int(revision))).rowcount
            if changed != 1:
                raise MailServiceError("Черновик уже отправляется или закрыт.")
        account = next((s for s in self.rules["accounts"] if s["address"] == payload["from"].lower()), None)
        if not account:
            self._clear_forward_blob(payload)
            with self._db() as db:
                db.execute("UPDATE reply_drafts SET state='failed',payload=? WHERE id=?",
                           (json.dumps(payload, ensure_ascii=False), draft_id))
            raise MailServiceError("Ящик отправителя больше не подключён.")
        box = self.account_get(account["id"])
        try:
            result = box.send_reply(payload)
        except Exception as exc:
            self._clear_forward_blob(payload)
            with self._db() as db:
                db.execute("UPDATE reply_drafts SET state='unknown',payload=?,result=?,updated_at=? WHERE id=?",
                           (json.dumps(payload, ensure_ascii=False), str(exc)[:500], time.time(), draft_id))
            raise MailServiceError("Результат передачи письма неизвестен. Повторно не отправляю — сначала проверьте «Отправленные».") from exc
        self._clear_forward_blob(payload)
        with self._db() as db:
            db.execute("UPDATE reply_drafts SET state='sent',payload=?,result=?,updated_at=? WHERE id=?",
                       (json.dumps(payload, ensure_ascii=False), json.dumps(result, ensure_ascii=False), time.time(), draft_id))
            original_key = row["original_key"]
            parts = original_key.split(":", 2)
            if len(parts) == 3 and payload.get("kind") != "forward":
                db.execute("UPDATE messages SET answered=1,updated_at=? WHERE account_id=? AND folder='INBOX' AND uidvalidity=? AND uid=?",
                           (time.time(), parts[0], parts[1], parts[2]))
        return {"state": "sent", "from": payload["from"], "to": payload["to"], "cc": payload["cc"],
                "subject": payload["subject"], "sent_copy": result.get("sent_copy", "unknown")}

    def draft_get(self, draft_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM reply_drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            raise MailServiceError("Черновик не найден.")
        return self._draft_view(row["id"], row["revision"], row["state"], json.loads(row["payload"]))
