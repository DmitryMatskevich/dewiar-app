"""Почта: подключение, проверка, письмо с вложением из папки ассистента, чтение входящих — через поддельные
SMTP и IMAP серверы (без шифрования, на 127.0.0.1)."""
import base64, email, os, socket, socketserver, sys, tempfile, threading, time
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg"); os.environ["DEWIAR_STATE_DIR"] = str(tmp / "st")
sys.path.insert(0, str(ROOT / "core"))
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
USER, PASS = "anna@example.com", "app-pass-123"
SENT = []


def port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class SMTP(socketserver.StreamRequestHandler):
    def w(self, s): self.wfile.write((s + "\r\n").encode())
    def handle(self):
        self.w("220 fake ESMTP"); authed = False
        while True:
            line = self.rfile.readline().decode(errors="replace").strip()
            if not line: return
            cmd = line.upper()
            if cmd.startswith("EHLO"): self.w("250-fake"); self.w("250 AUTH PLAIN LOGIN")
            elif cmd.startswith("HELO"): self.w("250 fake")
            elif cmd.startswith("AUTH PLAIN"):
                arg = line.split(" ", 2)[2] if len(line.split(" ")) > 2 else ""
                if not arg: self.w("334 "); arg = self.rfile.readline().decode().strip()
                _, u, p = base64.b64decode(arg).decode().split("\0")
                authed = (u, p) == (USER, PASS); self.w("235 ok" if authed else "535 bad credentials")
            elif cmd.startswith("MAIL") or cmd.startswith("RCPT"): self.w("250 ok" if authed else "530 auth")
            elif cmd == "DATA":
                self.w("354 go"); data = b""
                while True:
                    l = self.rfile.readline()
                    if l in (b".\r\n", b".\n"): break
                    data += l[1:] if l.startswith(b"..") else l
                SENT.append(data); self.w("250 queued")
            elif cmd == "QUIT": self.w("221 bye"); return
            else: self.w("250 ok")


def mk(uid, frm, subj, body, attach=None):
    m = EmailMessage(); m["From"] = frm; m["To"] = USER; m["Subject"] = subj; m["Date"] = "Thu, 01 Oct 2026 09:00:00 +0300"
    m.set_content(body)
    if attach: m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename=attach)
    return m.as_bytes()


BOX = {1: (mk(1, "Бухгалтерия <buh@corp.ru>", "Счёт за сентябрь", "Добрый день! Счёт на оплату во вложении.", "счёт.pdf"), True),
       2: (mk(2, "Мама <mama@mail.ru>", "Приезжай в субботу", "Жду тебя в субботу к обеду."), False)}


class IMAP(socketserver.StreamRequestHandler):
    def w(self, s): self.wfile.write((s + "\r\n").encode() if isinstance(s, str) else s)
    def handle(self):
        self.w("* OK IMAP4rev1 fake ready")
        while True:
            line = self.rfile.readline().decode(errors="replace").strip()
            if not line: return
            tag, _, rest = line.partition(" "); cmd = rest.upper()
            if cmd.startswith("CAPABILITY"): self.w("* CAPABILITY IMAP4rev1 AUTH=PLAIN"); self.w(f"{tag} OK done")
            elif cmd.startswith("LOGIN"):
                parts = rest.split(" ")
                u, p = parts[1].strip('"'), parts[2].strip('"')
                self.w(f"{tag} OK logged in" if (u, p) == (USER, PASS) else f"{tag} NO [AUTHENTICATIONFAILED] bad")
            elif cmd.startswith("EXAMINE") or cmd.startswith("SELECT"): self.w(f"* {len(BOX)} EXISTS"); self.w(f"{tag} OK [READ-ONLY] done")
            elif cmd.startswith("UID SEARCH"):
                ids = [str(k) for k, (_, seen) in BOX.items() if "UNSEEN" not in cmd or not seen]
                self.w("* SEARCH " + " ".join(ids)); self.w(f"{tag} OK done")
            elif cmd.startswith("UID FETCH"):
                uid = int(rest.split(" ")[2]); raw, seen = BOX.get(uid, (b"", False))
                if raw:
                    flags = r"\Seen" if seen else ""
                    structure = ' BODYSTRUCTURE ("APPLICATION" "PDF" ("NAME" "счёт.pdf") NIL NIL "BASE64" 5)' if uid == 1 else ""
                    self.w(f"* {uid} FETCH (UID {uid} FLAGS ({flags}) INTERNALDATE \"06-Nov-2026 10:00:00 +0300\"{structure} BODY[] {{{len(raw)}}}".encode() + b"\r\n" + raw + b")\r\n")
                self.w(f"{tag} OK done")
            elif cmd.startswith("LOGOUT"): self.w("* BYE"); self.w(f"{tag} OK bye"); return
            else: self.w(f"{tag} OK")


sp, ip = port(), port()
for P, H in ((sp, SMTP), (ip, IMAP)):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", P), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

from settings import Settings  # noqa: E402
from workspace import Workspace  # noqa: E402
from mail import Mail, MailError, guess, _imap_quote  # noqa: E402
from tools import Toolbox, t_read_emails  # noqa: E402

g = guess("ivan@yandex.ru")
ok(g["smtp_host"] == "smtp.yandex.ru" and g["imap_port"] == 993 and "yandex" in g["help"], "Яндекс: серверы и ссылка на пароль приложения — сами")
ok(guess("x@bk.ru")["smtp_host"] == "smtp.mail.ru" and guess("y@gmail.com")["imap_host"] == "imap.gmail.com", "Mail.ru и Gmail — сами")
s = Settings(); s.update({"workspace": {"path": str(tmp / "ws")}})
ws = Workspace(s); ws.ensure()
m = Mail(tmp / "mail.json", ws)
tb = Toolbox(s, None); tb.hooks["mail"] = m
ok(not tb.policy("send_email")[0], "без настроенной почты инструментов почты у модели нет")
try: m.save({"address": "не-адрес"}); ok(False, "плохой адрес")
except MailError: ok(True, "неверный адрес не принимается")
cfg = {"address": USER, "password": "wrong", "smtp_host": "127.0.0.1", "smtp_port": sp, "smtp_security": "none",
       "imap_host": "127.0.0.1", "imap_port": ip, "name": "Анна"}
m.save(cfg)
r = m.test()
ok("пароль приложения" in r["smtp"] and "пароль" in r["imap"], f"неверный пароль — понятно: {r['smtp'][:60]}")
pub = m.save({"password": PASS})
ok(pub["configured"] and "password" not in pub and pub["has_password"], "пароль сохранён, наружу не отдаётся")
ok(m.test() == {"smtp": "ok", "imap": "ok", "auth_rejected": False}, "проверка: отправка и чтение работают")
ok(tb.policy("send_email") == (True, True) and tb.policy("read_emails") == (True, False), "отправка — с подтверждением, чтение — сразу")
original_eml = b"From: client@example.org\r\nSubject: Original\r\n\r\nOriginal body\r\n"
m.data["provider"] = "gmail"  # Gmail сохраняет отправленное на сервере.
forward_result = m.send_reply({"to": ["client@example.org"], "cc": [], "subject": "Fwd: Original", "body": "Посмотрите.",
                               "forward_eml_b64": base64.b64encode(original_eml).decode("ascii"),
                               "forward_filename": "Fwd_Original.eml"})
forwarded = email.message_from_bytes(SENT[-1])
eml_part = next((part for part in forwarded.walk() if part.get_filename() == "Fwd_Original.eml"), None)
ok(forward_result["accepted_by_smtp"] and eml_part and eml_part.get_payload(decode=True) == original_eml,
   "пересылка отправляет исходное письмо MIME файлом .eml с вложениями")
reply_result = m.send_reply({"to": ["client@example.org"], "cc": [], "subject": "Re: Original",
                             "body": "Проверочный ответ", "in_reply_to": "<source@example.org>"})
reply_message = email.message_from_bytes(SENT[-1])
reply_sent_at = email.utils.parsedate_to_datetime(reply_message["Date"]).timestamp()
ok(reply_result["accepted_by_smtp"] and abs(reply_sent_at - time.time()) < 5,
   "дата заголовка отправленного ответа соответствует текущим часам, а не дате цитаты")
class MoveIMAP:
    capabilities = (b"IMAP4rev1", b"MOVE")
    def __init__(self): self.calls = []
    def select(self, folder, readonly=False): self.calls.append(("select", folder, readonly)); return "OK", []
    def uid(self, command, *args): self.calls.append((command, *args)); return "OK", [b"done"]
    def logout(self): return "BYE", []
moved_server = MoveIMAP()
with patch.object(m, "special_folder", return_value="Удалённые письма"), patch.object(m, "_imap", return_value=moved_server):
    moved = m.move_to_trash("42")
move_call = moved_server.calls[-1]
ok(moved["state"] == "moved" and move_call[0] == "MOVE" and move_call[1] == "42" and
   all(ord(c) < 128 for c in move_call[2]) and not any(call[0] == "EXPUNGE" for call in moved_server.calls),
   "удаление использует только UID MOVE в Корзину")
class UidPlusIMAP(MoveIMAP):
    capabilities = (b"IMAP4rev1", b"UIDPLUS")
uidplus_server = UidPlusIMAP()
with patch.object(m, "special_folder", return_value=""), patch.object(m, "_imap", return_value=uidplus_server):
    deleted = m.move_to_trash("77")
ok(deleted["state"] == "deleted" and uidplus_server.calls[-1] == ("EXPUNGE", "77"),
   "без Корзины удаляется только UID, UID EXPUNGE не трогает другие помеченные письма")
class UnsafeDeleteIMAP(MoveIMAP):
    capabilities = (b"IMAP4rev1",)
unsafe_server = UnsafeDeleteIMAP()
with patch.object(m, "special_folder", return_value=""), patch.object(m, "_imap", return_value=unsafe_server):
    try: m.move_to_trash("88"); safe_delete = False
    except MailError: safe_delete = not any(call[0] == "STORE" for call in unsafe_server.calls)
ok(safe_delete, "при отсутствии Корзины и UIDPLUS письмо не изменяется")
class TrashListIMAP(MoveIMAP):
    def list(self): return "OK", [f'(\\HasNoChildren) "/" {_imap_quote("Корзина")}'.encode()]
trash_list_server = TrashListIMAP()
with patch.object(m, "_imap", return_value=trash_list_server):
    localized_trash = m.special_folder("\\Trash")
ok(localized_trash == _imap_quote("Корзина")[1:-1], "локализованная папка Корзина найдена без SPECIAL-USE")
class FoldersIMAP:
    def list(self):
        return "OK", [b'(\\HasNoChildren \\Inbox) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "Sent"',
                       b'(\\HasNoChildren \\Drafts) "/" "Drafts"', b'(\\HasNoChildren \\Junk) "/" "Spam"',
                       b'(\\HasNoChildren) "/" "Clients"']
    def logout(self): return "BYE", []
folders_server = FoldersIMAP()
with patch.object(m, "_imap", return_value=folders_server):
    folders = m.folders()
ok([f["id"] for f in folders] == ["INBOX", "SENT", "DRAFTS", "JUNK", folders[-1]["id"]]
   and folders[-1]["name"] == "Clients" and folders[-1]["id"].startswith("F:"),
   "стандартные и пользовательские папки IMAP определяются по SPECIAL-USE")
(ws.root / "Заметки" / "Отчёт.md").write_text("# Отчёт\nВсё готово.", encoding="utf-8")
res = m.send("", "Отчёт по задаче", "Привет! Отчёт во вложении.", ["Заметки/Отчёт.md"])
msg = email.message_from_bytes(SENT[-1])
att = [p.get_filename() for p in msg.walk() if p.get_filename()]
ok(res["to"] == [USER] and "Отчёт по задаче" in str(email.header.make_header(email.header.decode_header(msg["Subject"]))) and att == ["Отчёт.md"],
   "«пришли мне»: письмо себе с вложением из папки ассистента")
try: m.send("", "x", "y", ["../../etc/passwd"]); ok(False, "выход из папки")
except Exception: ok(True, "вложить файл вне папки ассистента нельзя")
box = m.inbox(limit=5)
ok([x["uid"] for x in box] == ["2", "1"] and box[1]["files"] == ["счёт.pdf"] and box[0]["unread"] and not box[1]["unread"],
   "входящие: новые сверху, вложения и «непрочитано» видны")
ok(box[0]["received_at"] is not None and box[0]["received_at"] > email.utils.parsedate_to_datetime(box[0]["date"]).timestamp(),
   "время доставки берётся из IMAP INTERNALDATE отдельно от даты отправителя")
ok([x["uid"] for x in m.inbox(unread=True)] == ["2"], "только непрочитанные")
ok([x["uid"] for x in m.inbox(query="счёт")] == ["1"], "поиск по слову")
full = m.read("1")
ok("Счёт на оплату" in full["text"] and "Бухгалтерия" in full["from"], "письмо целиком")
out = t_read_emails({"limit": 5}, {"mail": m})
ok("Приезжай в субботу" in out and "●" in out, "инструмент read_emails — понятный список для модели")
ok(Mail(tmp / "mail.json", ws).configured(), "настройки почты переживают перезапуск")
m.forget()
ok(not m.configured() and not (tmp / "mail.json").exists(), "«Отключить» — всё стёрто")
