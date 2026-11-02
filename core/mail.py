"""Почта: отправить письмо (результат задачи, заметку, файл) и прочитать входящие — своим ящиком.

Любой ящик по SMTP/IMAP: Яндекс, Mail.ru, Gmail, Outlook, Rambler, iCloud или свой сервер (серверы подставляются
по домену адреса — guess()). Для больших почт нужен «пароль приложения» (обычный пароль они сторонним программам
не дают) — студия подсказывает, где его взять. Настройки — в ~/.config/dewiar/mail.json (0600); пароль в студию
не отдаётся (public()). Чтение не помечает письма прочитанными (папка открывается только для чтения, BODY.PEEK).
"""

import email
import email.utils
import base64
import html
import imaplib
import json
import logging
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import threading
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path

import mail_keychain as keychain
from settings import CONFIG_DIR

log = logging.getLogger("dewiar.mail")
PATH = CONFIG_DIR / "mail.json"
KEYCHAIN_SERVICE = "dewiar-mail"


def _keychain_get(account, interactive=False):
    """Пароль из Связки ключей или "" — без окон macOS, если interactive не задан (см. mail_keychain)."""
    state, secret = keychain.read(account, KEYCHAIN_SERVICE, interactive=interactive)
    return secret if state == keychain.OK else ""


def _keychain_set(account, secret):
    """Сохранить пароль -> ключ записи (обычно сам адрес) или "" не на macOS. Окон macOS не показывает."""
    if sys.platform != "darwin":
        return ""
    try:
        return keychain.write(account, secret, KEYCHAIN_SERVICE,
                              label=f"Dewiar — почта {str(account).split('#', 1)[0]}")
    except keychain.KeychainError as e:
        raise MailError(str(e)) from e


def _keychain_delete(account):
    if sys.platform == "darwin" and account:
        keychain.delete(account, KEYCHAIN_SERVICE)


# Что сказать пользователю, если пароль из Связки ключей не получен (состояния — mail_keychain).
CREDENTIAL_MESSAGES = {
    keychain.MISSING: "пароль ящика не сохранён — введите пароль приложения в Студия → Почта",
    keychain.NEEDS_ACCESS: "нет доступа к паролю в Связке ключей (Dewiar обновился) — введите пароль заново "
                           "или нажмите «Разрешить доступ» в Студия → Почта",
    keychain.LOCKED: "Связка ключей заблокирована — разблокируйте её и повторите",
    keychain.UNAVAILABLE: "Связка ключей macOS недоступна",
    "rejected": "почта не приняла сохранённый пароль — введите новый пароль приложения в Студия → Почта",
}

# почтовая служба → (smtp, порт, защита: ssl — TLS сразу, starttls — по команде; imap, порт; где взять пароль приложения)
# DOMAINS ниже: домен адреса → служба.
PRESETS = {
    "yandex": ("smtp.yandex.ru", 465, "ssl", "imap.yandex.ru", 993, "https://id.yandex.ru/security/app-passwords"),
    "mailru": ("smtp.mail.ru", 465, "ssl", "imap.mail.ru", 993, "https://account.mail.ru/user/2-step-auth/passwords/"),
    "gmail": ("smtp.gmail.com", 465, "ssl", "imap.gmail.com", 993, "https://myaccount.google.com/apppasswords"),
    "outlook": ("smtp-mail.outlook.com", 587, "starttls", "outlook.office365.com", 993, "https://account.live.com/proofs/AppPassword"),
    "rambler": ("smtp.rambler.ru", 465, "ssl", "imap.rambler.ru", 993, ""),
    "icloud": ("smtp.mail.me.com", 587, "starttls", "imap.mail.me.com", 993, "https://account.apple.com"),
    "yahoo": ("smtp.mail.yahoo.com", 465, "ssl", "imap.mail.yahoo.com", 993, ""),
}
DOMAINS = {"yandex.ru": "yandex", "ya.ru": "yandex", "yandex.com": "yandex", "yandex.by": "yandex", "yandex.kz": "yandex",
           "narod.ru": "yandex", "mail.ru": "mailru", "bk.ru": "mailru", "inbox.ru": "mailru", "list.ru": "mailru",
           "internet.ru": "mailru", "gmail.com": "gmail", "googlemail.com": "gmail", "outlook.com": "outlook",
           "hotmail.com": "outlook", "live.com": "outlook", "rambler.ru": "rambler", "lenta.ru": "rambler",
           "icloud.com": "icloud", "me.com": "icloud", "yahoo.com": "yahoo"}


class MailError(Exception):
    pass


class MailAuthError(MailError):
    """Сервер не принял логин или пароль."""


class MailCredentialError(MailError):
    """Пароль не получен из Связки ключей (или известно, что сервер его не принимает); на сервер не ходили."""

    def __init__(self, state):
        super().__init__(CREDENTIAL_MESSAGES.get(state) or CREDENTIAL_MESSAGES[keychain.UNAVAILABLE])
        self.state = state


def guess(address):
    """Адрес → настройки серверов (по домену; неизвестный домен — smtp.<домен>:465 и imap.<домен>:993)."""
    dom = (address or "").rsplit("@", 1)[-1].lower().strip()
    key = DOMAINS.get(dom)
    if key:
        s, sp, sec, i, ip, help_url = PRESETS[key]
        return {"smtp_host": s, "smtp_port": sp, "smtp_security": sec, "imap_host": i, "imap_port": ip,
                "help": help_url, "provider": key}
    return {"smtp_host": f"smtp.{dom}" if dom else "", "smtp_port": 465, "smtp_security": "ssl",
            "imap_host": f"imap.{dom}" if dom else "", "imap_port": 993, "help": "", "provider": ""}


def _dec(v):
    """Заголовок письма в кодировке MIME (=?utf-8?b?…?=) → обычная строка; не разбирается — возвращаем как есть."""
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:  # noqa: BLE001
        return v or ""


def _text_of(msg, limit=20000):
    """Письмо -> (текст до limit символов, имена вложений). Текст — из text/plain, а если его нет — html без разметки.
    Часть с именем файла считается вложением, даже если это текст."""
    plain, htm, files = [], [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.is_multipart():
            continue
        fn = part.get_filename()
        if fn:
            files.append(_dec(fn))
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True) or b""
            txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        (plain if ctype == "text/plain" else htm if ctype == "text/html" else []).append(txt)
    body = "\n".join(plain).strip()
    if not body and htm:
        h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", "\n".join(htm))
        h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", h)
        body = html.unescape(re.sub(r"<[^>]+>", " ", h))
        body = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", body)).strip()
    return body[:limit], files


def _imap_quote(value):
    """Quote mailbox names, encoding Unicode as IMAP modified UTF-7."""
    text = str(value)
    if any(ord(char) > 0x7e for char in text):
        encoded, run = [], []
        def flush():
            if run:
                chunk = base64.b64encode("".join(run).encode("utf-16-be")).decode("ascii").rstrip("=").replace("/", ",")
                encoded.append("&" + chunk + "-")
                run.clear()
        for char in text:
            if 0x20 <= ord(char) <= 0x7e:
                flush()
                encoded.append("&-" if char == "&" else char)
            else:
                run.append(char)
        flush()
        text = "".join(encoded)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _imap_decode_utf7(value):
    """Decode a mailbox name from IMAP modified UTF-7 for matching localized Trash names."""
    def replace(match):
        token = match.group(1)
        if not token:
            return "&"
        try:
            encoded = token.replace(",", "/")
            return base64.b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4)).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)
    return re.sub(r"&([^-]*)-", replace, str(value))


def _mailbox_uidvalidity(connection):
    try:
        values = connection.response("UIDVALIDITY")[1]
        if values and values[0] is not None:
            return values[0].decode() if isinstance(values[0], bytes) else str(values[0])
    except (IndexError, TypeError, AttributeError, imaplib.IMAP4.error):
        pass
    return "0"


class Mail:
    """Один ящик. Соединение открывается на каждую операцию и сразу закрывается — держать его незачем."""

    def __init__(self, path=PATH, workspace=None, migrate_keychain=True):
        self.path, self.ws = path, workspace
        self._lock = threading.Lock()
        # Пароль читается из Связки ключей один раз за запуск — при первом входе на сервер, как у Apple Mail.
        self._secret = ""
        self._secret_state = ""        # "" — нет проблем; иначе состояние mail_keychain или "rejected"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        legacy = self.data.get("password")
        if migrate_keychain and legacy and sys.platform == "darwin":
            try:
                # Не записывать промежуточный JSON с паролем: при аварии между записью
                # в Keychain и очисткой файла секрет иначе остался бы в открытом виде.
                if self._store_secret(legacy, persist=False):
                    self.data.pop("password", None)
                    self._write_config()
            except MailError:
                log.exception("Не удалось перенести пароль почты в Связку ключей")

    # ---------- настройки ----------

    def configured(self):
        """Адрес, сервер и пароль есть. Пароль при этом не читается — только проверяется наличие записи."""
        d = self.data
        return bool(d.get("address") and d.get("smtp_host") and self._has_secret())

    def _ref(self):
        """Ключ записи в Связке ключей: адрес или адрес#время, если прежняя запись недоступна этой сборке."""
        return self.data.get("keychain_account") or self.data.get("address")

    def _has_secret(self):
        if self.data.get("password") or self._secret:
            return True
        if sys.platform != "darwin" or not self.data.get("address"):
            return False
        # Заблокированная Связка ключей — запись, скорее всего, есть; причину покажет credential_state().
        return keychain.exists(self._ref(), KEYCHAIN_SERVICE) in (keychain.OK, keychain.LOCKED)

    def credential_state(self):
        """ok — пароль работает в этом запуске; saved — сохранён, ещё не читался; missing; needs_access — macOS
        не отдаёт запись без разрешения; locked; rejected — сервер не принял пароль; unavailable."""
        if self._secret_state:
            return self._secret_state
        if self.data.get("password") or self._secret:
            return keychain.OK
        if not self.data.get("address") or sys.platform != "darwin":
            return keychain.MISSING
        found = keychain.exists(self._ref(), KEYCHAIN_SERVICE)
        return "saved" if found == keychain.OK else found

    def _password(self):
        """Пароль для входа. Из Связки ключей — без окон macOS; не получилось — MailCredentialError, на сервер
        не идём (пустой или заведомо неверный пароль только увеличит счётчик неудачных входов у почты)."""
        if self._secret_state == "rejected":
            raise MailCredentialError("rejected")
        if self.data.get("password"):
            return self.data["password"]
        if self._secret:
            return self._secret
        if sys.platform != "darwin":
            raise MailCredentialError(keychain.MISSING)
        state, secret = keychain.read(self._ref(), KEYCHAIN_SERVICE)
        if state == keychain.OK and secret:
            self._secret, self._secret_state = secret, ""
            return secret
        self._secret_state = state
        raise MailCredentialError(state)

    def _store_secret(self, secret, persist=True):
        """Записать пароль в Связку ключей (без окон) и запомнить ключ записи -> False не на macOS."""
        old = self._ref()
        ref = _keychain_set(old, secret)
        if not ref:
            return False
        if ref != old:
            _keychain_delete(old)                  # обычно macOS не даёт удалить чужую запись — это не ошибка
        if ref == self.data.get("address"):
            self.data.pop("keychain_account", None)
        else:
            self.data["keychain_account"] = ref
        self._secret, self._secret_state = secret, ""
        if persist:
            self._write_config()
        return True

    def unlock(self):
        """Явное действие пользователя: прочитать пароль с окном macOS («Разрешить» / «Разрешать всегда»)
        и переложить его в запись этой сборки, чтобы следующий запуск прочитал её без окна."""
        if sys.platform != "darwin":
            raise MailError("Связка ключей есть только в macOS")
        with self._lock:
            state, secret = keychain.read(self._ref(), KEYCHAIN_SERVICE, interactive=True)
            if state != keychain.OK or not secret:
                self._secret_state = state
                raise MailCredentialError(state)
            self._secret, self._secret_state = secret, ""
            try:
                self._store_secret(secret)
            except MailError:
                log.exception("Не удалось пересохранить пароль почты в Связку ключей")
        return self.public()

    def _write_config(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def public(self):
        d = dict(self.data)
        d.pop("password", None)
        d.pop("keychain_account", None)
        d["has_password"] = self._has_secret()
        d["configured"] = bool(d.get("address") and d.get("smtp_host") and d["has_password"])
        d["credential_state"] = self.credential_state()
        if d.get("address") and not d.get("help"):
            d["help"] = guess(d["address"]).get("help", "")
        return d

    def save(self, patch):
        """Сохранить настройки из студии -> public(). Берутся только известные поля; файл пишется атомарно, права 0600."""
        with self._lock:
            patch = dict(patch or {})
            if not patch.get("password"):
                patch.pop("password", None)                     # пустое поле — пароль не меняем
            addr = (patch.get("address") or self.data.get("address") or "").strip()
            if addr and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", addr):
                raise MailError("это не похоже на адрес почты")
            if addr != self.data.get("address") and not patch.get("smtp_host"):
                patch = {**guess(addr), **patch}                 # новый адрес — подставляем серверы сами
            previous_address, previous_ref = self.data.get("address"), self._ref()
            secret = patch.pop("password", None)
            self.data.update({k: v for k, v in patch.items() if k in (
                "address", "name", "login", "smtp_host", "smtp_port", "smtp_security", "imap_host",
                "imap_port", "help", "provider")})
            self.data["address"] = addr
            if previous_address and previous_address != addr:
                _keychain_delete(previous_ref)
                self.data.pop("keychain_account", None)
                self._secret, self._secret_state = "", ""
            if secret and self._store_secret(secret):
                self.data.pop("password", None)
            elif secret:
                self.data["password"] = secret          # не macOS: файл 0600, как раньше
                self._secret, self._secret_state = "", ""
            self._write_config()
        return self.public()

    def forget(self):
        with self._lock:
            account = self._ref()
            self.data = {}
            self._secret, self._secret_state = "", ""
            _keychain_delete(account)
            try:
                self.path.unlink()
            except OSError:
                pass
        return self.public()

    # ---------- соединения ----------

    def _login(self):
        # Логин обычно совпадает с адресом; отдельное поле — для своих серверов, где это не так.
        return self.data.get("login") or self.data.get("address")

    def _auth_rejected(self, candidate):
        """Сервер не принял пароль. Сохранённый пароль больше не пробуем, пока пользователь не введёт новый."""
        if candidate is None:
            self._secret, self._secret_state = "", "rejected"

    def _auth_ok(self, candidate):
        if candidate is None and self._secret_state == "rejected":
            self._secret_state = ""

    def _smtp(self, candidate=None):
        d = self.data
        host, port, sec = d.get("smtp_host"), int(d.get("smtp_port") or 465), d.get("smtp_security") or "ssl"
        password = self._password() if candidate is None else candidate
        try:
            if sec == "ssl":
                s = smtplib.SMTP_SSL(host, port, timeout=25, context=ssl.create_default_context())
            else:
                s = smtplib.SMTP(host, port, timeout=25)
                s.ehlo()
                if sec == "starttls":
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
            s.login(self._login(), password)
            self._auth_ok(candidate)
            return s
        except smtplib.SMTPAuthenticationError as e:
            self._auth_rejected(candidate)
            raise MailAuthError("почта не приняла логин или пароль — нужен «пароль приложения», не обычный пароль") from e
        except (OSError, smtplib.SMTPException) as e:
            raise MailError(f"не удалось подключиться к {host}:{port} — {e}") from e

    def _imap(self, candidate=None):
        """Вход в IMAP. Порт 993 — TLS сразу; другой порт — STARTTLS, и без него не входим: пароль открытым текстом
        по сети не отправляем (исключение — свой сервер на этой же машине)."""
        d = self.data
        host, port = d.get("imap_host"), int(d.get("imap_port") or 993)
        password = self._password() if candidate is None else candidate
        try:
            if port == 993:
                m = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=25)
            else:
                m = imaplib.IMAP4(host, port, timeout=25)
                if host not in ("127.0.0.1", "localhost", "::1"):
                    try:
                        m.starttls(ssl.create_default_context())
                    except (imaplib.IMAP4.error, OSError) as e:
                        raise MailError(f"{host}:{port} не умеет STARTTLS — пароль открытым текстом не отправляю; укажите порт 993") from e
        except imaplib.IMAP4.error as e:
            raise MailError(f"{host}:{port}: {e}") from e
        except OSError as e:
            raise MailError(f"не удалось подключиться к {host}:{port} — {e}") from e
        try:
            m.login(self._login(), password)
            self._auth_ok(candidate)
            return m
        except imaplib.IMAP4.error as e:
            self._auth_rejected(candidate)
            try:
                m.shutdown()
            except OSError:
                pass
            raise MailAuthError("почта не приняла логин или пароль (IMAP) — нужен «пароль приложения»") from e
        except OSError as e:
            raise MailError(f"не удалось подключиться к {host}:{port} — {e}") from e

    def test(self, candidate=None):
        """Проверить вход в SMTP и IMAP -> {"smtp": "ok" или текст ошибки, "imap": …, "auth_rejected": bool}.
        candidate — новый пароль, который проверяется до сохранения (сохранённый при этом не трогается)."""
        d = self.data
        if not (d.get("address") and d.get("smtp_host") and (candidate or self._has_secret())):
            raise MailError("сначала укажите адрес и пароль приложения")
        out = {"smtp": "", "imap": "", "auth_rejected": False}
        try:
            self._smtp(candidate).quit()
            out["smtp"] = "ok"
        except MailAuthError as e:
            out["smtp"], out["auth_rejected"] = str(e), True
        except MailError as e:
            out["smtp"] = str(e)
        if self.data.get("imap_host"):
            try:
                m = self._imap(candidate)
                m.logout()
                out["imap"] = "ok"
            except MailAuthError as e:
                out["imap"], out["auth_rejected"] = str(e), True
            except MailError as e:
                out["imap"] = str(e)
        return out

    # ---------- письма ----------

    def send(self, to="", subject="", body="", attachments=(), attachment_data=(), message_id=""):
        """Отправить письмо -> {to, subject, attachments}. Без получателя — себе на свой адрес.
        Вложения — пути в папке ассистента: ws.safe() не даёт выйти за её пределы. Предел 20 МБ на файл — столько
        принимают почтовые службы с запасом на кодирование вложения."""
        if not self.configured():
            raise MailError("почта не подключена (студия → «Подключение» → «Почта»)")
        to = [x.strip() for x in re.split(r"[,;\s]+", to or "") if x.strip()] or [self.data["address"]]
        for x in to:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", x):
                raise MailError(f"неверный адрес: {x}")
        msg = EmailMessage()
        me = self.data["address"]
        msg["From"] = email.utils.formataddr((self.data.get("name") or "Dewiar", me))
        msg["To"] = ", ".join(to)
        msg["Subject"] = (subject or "Сообщение от Dewiar").strip()[:200]
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = (f"<{message_id}@{me.split('@')[-1]}>" if re.fullmatch(r'[a-f0-9]{32}',message_id)
                             else email.utils.make_msgid(domain=me.split("@")[-1]))
        msg.set_content((body or "").strip() + "\n")
        names = []
        for rel in attachments or []:
            p = self.ws.safe(str(rel)) if self.ws else Path(rel)
            if not p.is_file():
                raise MailError(f"нет файла {rel} в папке ассистента")
            if p.stat().st_size > 20 * 1024 * 1024:
                raise MailError(f"файл {p.name} больше 20 МБ — почта его не примет")
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            main, sub = ctype.split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
            names.append(p.name)
        for filename,ctype,data in attachment_data:
            if not isinstance(data,bytes) or len(data)>20*1024*1024:
                raise MailError('Вложение отчёта больше 20 МБ или имеет неверный формат')
            main,sub=ctype.split('/',1)
            filename=Path(filename).name
            if main=='text':
                msg.add_attachment(data.decode('utf-8'),subtype=sub,charset='utf-8',filename=filename)
            else:
                msg.add_attachment(data,maintype=main,subtype=sub,filename=filename)
            names.append(filename)
        s = self._smtp()
        try:
            s.send_message(msg)
        except smtplib.SMTPException as e:
            raise MailError(f"письмо не отправилось: {e}") from e
        finally:
            try:
                s.quit()
            except Exception:  # noqa: BLE001
                pass
        log.info("✉️  Письмо «%s» → %s%s", msg["Subject"], ", ".join(to), f" (вложения: {', '.join(names)})" if names else "")
        return {"to": to, "subject": msg["Subject"], "attachments": names}

    def send_reply(self, draft):
        """Send an exact, already-confirmed reply-all payload with RFC threading headers."""
        if not self.configured():
            raise MailError("Почтовый ящик отключён.")
        to, cc = list(draft.get("to") or []), list(draft.get("cc") or [])
        if not to or any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", x) for x in to + cc):
            raise MailError("В черновике есть неверный или пустой адрес получателя.")
        msg = EmailMessage()
        me = self.data["address"]
        msg["From"] = email.utils.formataddr((self.data.get("name") or "", me))
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = str(draft.get("subject") or "")[:200]
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=me.split("@")[-1])
        if draft.get("in_reply_to"):
            msg["In-Reply-To"] = draft["in_reply_to"]
        refs = str(draft.get("references") or "").strip()
        if refs:
            msg["References"] = refs[-9000:]
        body = str(draft.get("body") or "")
        msg.set_content(body)
        html_body = "<div>" + "<br>".join(html.escape(line) for line in body.splitlines()) + "</div>"
        msg.add_alternative(html_body, subtype="html")
        forwarded = draft.get("forward_eml_b64")
        if forwarded:
            try:
                raw_forward = base64.b64decode(forwarded, validate=True)
            except (ValueError, TypeError) as exc:
                raise MailError("Вложение пересылаемого письма повреждено.") from exc
            if len(raw_forward) > 18 * 1024 * 1024:
                raise MailError("Исходное письмо больше 18 МБ и не помещается во вложение пересылки.")
            filename = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(draft.get("forward_filename") or "Пересланное письмо.eml"))[:120]
            if not filename.lower().endswith(".eml"):
                filename += ".eml"
            msg.add_attachment(raw_forward, maintype="application", subtype="octet-stream", filename=filename)
        s = self._smtp()
        try:
            refused = s.send_message(msg)
            if refused:
                raise MailError("Сервер не принял адресатов: " + ", ".join(refused.keys()))
        except MailError:
            raise
        except (OSError, smtplib.SMTPException) as e:
            raise MailError(f"результат передачи письма через SMTP неизвестен: {e}") from e
        finally:
            try:
                s.quit()
            except Exception:  # noqa: BLE001
                pass
        sent_copy = "server"
        if self.data.get("provider") != "gmail" and self.data.get("sent_copy", "auto") != "off":
            try:
                folder = self.special_folder("\\Sent")
                if folder:
                    m = self._imap()
                    try:
                        typ, data = m.append(folder, "(\\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
                        sent_copy = "appended" if typ == "OK" else "append_failed"
                        if typ != "OK":
                            log.warning("IMAP APPEND вернул %s: %s", typ, data)
                    finally:
                        m.logout()
                else:
                    sent_copy = "not_found"
            except Exception as e:  # SMTP accepted; APPEND failure is reported separately, never resend.
                log.warning("Ответ отправлен, но копию не удалось сохранить в Sent: %s", e)
                sent_copy = "append_failed"
        return {"to": to, "cc": cc, "subject": msg["Subject"], "message_id": msg["Message-ID"],
                "sent_copy": sent_copy, "accepted_by_smtp": True}

    def uidvalidity(self, folder="INBOX"):
        m = self._imap()
        try:
            typ, _ = m.select(folder, readonly=True)
            if typ != "OK":
                raise MailError(f"нет папки {folder}")
            return _mailbox_uidvalidity(m)
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass

    def special_folder(self, special_use="\\Sent"):
        """Return the IMAP folder carrying a SPECIAL-USE flag such as \\Sent."""
        m = self._imap()
        try:
            typ, rows = m.list()
            if typ != "OK":
                return ""
            candidates = []
            for row in rows or []:
                raw = row.decode("utf-8", errors="replace") if isinstance(row, bytes) else str(row)
                match = re.search(r'"((?:\\.|[^"])*)"\s*$', raw)
                if match:
                    mailbox = match.group(1).replace('\\"', '"').replace('\\\\', '\\')
                else:
                    # IMAP permits an unquoted final mailbox token.
                    mailbox = raw.rsplit(" ", 1)[-1].strip('"')
                if special_use.casefold() in raw.casefold():
                    return mailbox
                candidates.append(mailbox)
            if special_use.casefold() == "\\trash":
                names = {"trash", "deleted", "deleted items", "deleted messages", "bin", "корзина", "удалённые", "удаленные"}
                for mailbox in candidates:
                    decoded = _imap_decode_utf7(mailbox).replace("\\", "/")
                    leaf = re.split(r"[/.]", decoded)[-1].strip().casefold()
                    if leaf in names:
                        return mailbox
            return ""
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass

    def inbox(self, limit=10, unread=False, query="", folder="INBOX", since_days=0):
        """Последние письма: [{uid, from, subject, date, snippet, files, unread}] — новые сверху.
        Поиск query — по отправителю, теме и тексту, на нашей стороне, среди limit×4 последних писем."""
        m = self._imap()
        try:
            typ, _ = m.select(folder, readonly=True)
            if typ != "OK":
                raise MailError(f"нет папки {folder}")
            # Исключаем сообщения, уже отправленные в корзину (в том числе помеченные
            # \Deleted на серверах без безопасной UID EXPUNGE).
            crit = ["UNSEEN", "UNDELETED"] if unread else ["UNDELETED"]
            if since_days:
                from datetime import datetime, timedelta
                since = (datetime.now() - timedelta(days=max(1, int(since_days)))).strftime("%d-%b-%Y")
                crit += ["SINCE", since]
            typ, data = m.uid("search", None, *crit)
            uids = (data[0] or b"").split()[::-1]
            out, q = [], (query or "").lower().strip()
            for uid in uids[: max(limit * (8 if q else 1), limit)]:
                typ, d = m.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES REPLY-TO DELIVERED-TO X-ORIGINAL-TO ENVELOPE-TO AUTHENTICATION-RESULTS)] BODYSTRUCTURE)")
                raw = next((x[1] for x in d if isinstance(x, tuple) and isinstance(x[1], bytes)), b"")
                structure = b" ".join(x[0] if isinstance(x, tuple) and isinstance(x[0], bytes)
                                       else x if isinstance(x, bytes) else b"" for x in d)
                names = []
                encoded_names = re.findall(rb'(?:FILENAME|NAME)"?\s+"((?:\\.|[^"])*)"', structure, re.I)
                encoded_names += re.findall(rb'(?:filename|name)\s*=\s*"([^"]+)"', raw, re.I)
                for encoded_name in encoded_names:
                    try:
                        name = encoded_name.replace(b'\\"', b'"').decode("utf-8", errors="replace")
                        if name and name not in names:
                            names.append(name)
                    except (UnicodeDecodeError, ValueError):
                        pass
                flags = " ".join(x.decode(errors="ignore") if isinstance(x, bytes) else "" for x in d if not isinstance(x, tuple))
                flags += " ".join(x[0].decode(errors="ignore") for x in d if isinstance(x, tuple))
                msg = email.message_from_bytes(raw)
                item = {"uid": uid.decode(), "from": _dec(msg.get("From")), "to": _dec(msg.get("To")), "cc": _dec(msg.get("Cc")),
                        "reply_to": _dec(msg.get("Reply-To")), "message_id": msg.get("Message-ID", ""),
                        "in_reply_to": msg.get("In-Reply-To", ""), "references": msg.get("References", ""),
                        "delivered_to": msg.get("Delivered-To", ""), "x_original_to": msg.get("X-Original-To", ""),
                        "authentication_results": _dec(msg.get("Authentication-Results", "")),
                        "subject": _dec(msg.get("Subject")) or "(без темы)",
                        "date": msg.get("Date", ""), "snippet": "", "files": names,
                        "unread": "\\Seen" not in flags, "answered": "\\Answered" in flags}
                if q and q not in (item["from"] + " " + item["subject"] + " " + item["to"] + " " + item["cc"]).lower():
                    continue
                out.append(item)
                if len(out) >= limit:
                    break
            return out
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass

    def read(self, uid, folder="INBOX", expected_uidvalidity=""):
        """Письмо целиком по uid из inbox() -> {uid, from, to, subject, date, text, files}."""
        uid = str(uid)
        if not re.fullmatch(r"[0-9]+", uid):
            raise MailError("Номер письма должен быть числовым UID.")
        m = self._imap()
        try:
            typ, _ = m.select(folder, readonly=True)
            if typ != "OK":
                raise MailError(f"нет папки {folder}")
            if expected_uidvalidity and _mailbox_uidvalidity(m) != str(expected_uidvalidity):
                raise MailError("Папка входящих изменилась; обновите список писем.")
            typ, d = m.uid("fetch", uid.encode(), "(BODY.PEEK[])")
            raw = next((x[1] for x in d if isinstance(x, tuple)), b"")
            if not raw:
                raise MailError("такого письма нет")
            msg = email.message_from_bytes(raw)
            body, files = _text_of(msg)
            return {"uid": str(uid), "from": _dec(msg.get("From")), "to": _dec(msg.get("To")), "cc": _dec(msg.get("Cc")),
                    "reply_to": _dec(msg.get("Reply-To")), "message_id": msg.get("Message-ID", ""),
                    "in_reply_to": msg.get("In-Reply-To", ""),
                    "references": msg.get("References", ""), "delivered_to": msg.get("Delivered-To", ""),
                    "x_original_to": msg.get("X-Original-To", ""), "authentication_results": _dec(msg.get("Authentication-Results", "")),
                    "subject": _dec(msg.get("Subject")),
                    "date": msg.get("Date", ""), "text": body, "files": files}
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass

    def read_raw(self, uid, folder="INBOX", expected_uidvalidity=""):
        """Получить исходный MIME для пересылки как .eml, не раскрывая его в обычном письме UI."""
        uid = str(uid)
        if not re.fullmatch(r"[0-9]+", uid):
            raise MailError("Номер письма должен быть числовым UID.")
        m = self._imap()
        try:
            typ, _ = m.select(folder, readonly=True)
            if typ != "OK":
                raise MailError(f"нет папки {folder}")
            if expected_uidvalidity and _mailbox_uidvalidity(m) != str(expected_uidvalidity):
                raise MailError("Папка входящих изменилась; обновите список писем.")
            typ, data = m.uid("fetch", uid.encode(), "(BODY.PEEK[])")
            raw = next((part[1] for part in data if isinstance(part, tuple) and isinstance(part[1], bytes)), b"")
            if typ != "OK" or not raw:
                raise MailError("такого письма нет")
            if len(raw) > 18 * 1024 * 1024:
                raise MailError("Исходное письмо больше 18 МБ: переслать его с сохранением вложений нельзя.")
            return raw
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass

    def move_to_trash(self, uid, folder="INBOX", expected_uidvalidity=""):
        """Move one UID to Trash; never use global EXPUNGE, which could erase other deleted messages."""
        uid = str(uid)
        if not re.fullmatch(r"[0-9]+", uid):
            raise MailError("Номер письма должен быть числовым UID.")
        trash = self.special_folder("\\Trash")
        m = self._imap()
        try:
            typ, _ = m.select(folder, readonly=False)
            if typ != "OK":
                raise MailError(f"нет папки {folder}")
            if expected_uidvalidity and _mailbox_uidvalidity(m) != str(expected_uidvalidity):
                raise MailError("Папка входящих изменилась; удаление остановлено.")
            capabilities = {x.decode("ascii", errors="ignore").upper() if isinstance(x, bytes) else str(x).upper()
                            for x in (getattr(m, "capabilities", ()) or ())}
            if trash and "MOVE" in capabilities:
                typ, data = m.uid("MOVE", uid, _imap_quote(trash))
                if typ == "OK":
                    return {"state": "moved", "folder": trash}
                raise MailError("Сервер не переместил письмо в Корзину: " + str(data)[:300])
            if trash:
                typ, data = m.uid("COPY", uid, _imap_quote(trash))
                if typ != "OK":
                    raise MailError("Не удалось скопировать письмо в Корзину: " + str(data)[:300])
                typ, data = m.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
                if typ != "OK":
                    raise MailError("Копия попала в Корзину, но сервер не пометил исходное письмо удалённым: " + str(data)[:300])
                if "UIDPLUS" in capabilities:
                    typ, data = m.uid("EXPUNGE", uid)
                    if typ != "OK":
                        raise MailError("Письмо в Корзине, но сервер не завершил удаление исходной копии: " + str(data)[:300])
                return {"state": "moved", "folder": trash}
            if "UIDPLUS" in capabilities:
                typ, data = m.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
                if typ == "OK":
                    typ, data = m.uid("EXPUNGE", uid)
                if typ == "OK":
                    return {"state": "deleted", "folder": ""}
                raise MailError("Сервер не удалил письмо: " + str(data)[:300])
            raise MailError("На сервере не найдена Корзина и нет безопасного удаления одного письма. Ничего не изменено.")
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass
