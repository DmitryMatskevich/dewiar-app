"""Telegram через прокси: SOCKS5 (с логином) и HTTP; ссылка t.me/socks превращается в socks5://; MTProto-прокси —
понятный отказ; нерабочий прокси не сохраняется; после отключения бота прокси остаётся; пароль прокси в студию не уходит.
Telegram и прокси — поддельные, всё на 127.0.0.1."""
import json, os, select, socket, struct, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
for k in [k for k in os.environ if k.lower().endswith("_proxy")]:
    os.environ.pop(k)
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg")
sys.path.insert(0, str(ROOT / "core"))
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
TOKEN = "123456789:AAHfakeTokenForDewiarTests_abcdefghijk"
SEEN = {"tg": 0, "socks": 0, "http": 0}


class TG(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _out(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        SEEN["tg"] += 1
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        m = self.path.rsplit("/", 1)[-1]
        if m == "getMe":
            return self._out({"ok": True, "result": {"id": 1, "username": "test_bot", "first_name": "Тест"}})
        if m == "getUpdates":
            time.sleep(0.3)
            return self._out({"ok": True, "result": []})
        return self._out({"ok": True, "result": True})

    def do_GET(self):
        SEEN["tg"] += 1
        self._out({"ok": True, "result": {"id": 1, "username": "test_bot", "first_name": "Тест"}})


def pipe(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 30)
            if not r:
                return
            for s in r:
                d = s.recv(65536)
                if not d:
                    return
                (b if s is a else a).sendall(d)
    except OSError:
        return


class Socks(StreamRequestHandler):
    """SOCKS5 с логином user/pass (RFC 1928/1929), только CONNECT."""
    def handle(self):
        c = self.connection
        ver, n = c.recv(2)
        methods = c.recv(n)
        if 2 not in methods:
            c.sendall(b"\x05\xff"); return
        c.sendall(b"\x05\x02")
        c.recv(1); ul = c.recv(1)[0]; user = c.recv(ul); pl = c.recv(1)[0]; pw = c.recv(pl)
        if (user, pw) != (b"anna", b"s3cret"):
            c.sendall(b"\x01\x01"); return
        c.sendall(b"\x01\x00")
        ver, cmd, _, atyp = c.recv(4)
        if atyp == 1:
            host = socket.inet_ntoa(c.recv(4))
        elif atyp == 3:
            host = c.recv(c.recv(1)[0]).decode()
        else:
            return
        port = struct.unpack(">H", c.recv(2))[0]
        SEEN["socks"] += 1
        up = socket.create_connection((host, port))
        c.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", port))
        pipe(c, up)


class HttpProxy(StreamRequestHandler):
    """HTTP-прокси: запрос с полным адресом → пересылаем на сервер."""
    def handle(self):
        line = self.rfile.readline().decode()
        method, url, ver = line.split()
        hdrs = []
        while True:
            h = self.rfile.readline()
            if h in (b"\r\n", b"\n", b""):
                break
            hdrs.append(h)
        from urllib.parse import urlsplit
        u = urlsplit(url)
        SEEN["http"] += 1
        up = socket.create_connection((u.hostname, u.port or 80))
        n = next((int(h.split(b":", 1)[1]) for h in hdrs if h.lower().startswith(b"content-length:")), 0)
        body = self.rfile.read(n) if n else b""
        up.sendall(f"{method} {u.path or '/'}{'?' + u.query if u.query else ''} {ver}\r\n".encode() + b"".join(hdrs) + b"\r\n" + body)
        pipe(self.connection, up)


def serve(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


ThreadingTCPServer.allow_reuse_address = True
ThreadingTCPServer.daemon_threads = True
ThreadingTCPServer.handle_error = lambda *a: None
tg_port = serve(ThreadingHTTPServer(("127.0.0.1", 0), TG))
socks_port = serve(ThreadingTCPServer(("127.0.0.1", 0), Socks))
http_port = serve(ThreadingTCPServer(("127.0.0.1", 0), HttpProxy))
API = f"http://127.0.0.1:{tg_port}"

from telegram import Telegram, TelegramError, normalize_proxy  # noqa: E402

ok(normalize_proxy(f"tg://socks?server=127.0.0.1&port={socks_port}&user=anna&pass=s3cret") == f"socks5://anna:s3cret@127.0.0.1:{socks_port}"
   and normalize_proxy("10.0.0.5:3128") == "http://10.0.0.5:3128", "ссылка t.me/socks → socks5://; «адрес:порт» → http://")
try:
    normalize_proxy("tg://proxy?server=1.2.3.4&port=443&secret=ee00"); mt = ""
except TelegramError as e:
    mt = str(e)
ok("MTProto" in mt, "MTProto-прокси — понятный отказ: он только для приложения Telegram")

tg = Telegram(path=tmp / "cfg/telegram.json")
tg.connect(TOKEN, API)
tg.stop()
SEEN.update(tg=0, socks=0, http=0)
pub = tg.set_proxy(f"t.me/socks?server=127.0.0.1&port={socks_port}&user=anna&pass=s3cret")
tg._call("sendMessage", chat_id=1, text="привет")
time.sleep(1.0)
ok(SEEN["socks"] >= 1 and SEEN["tg"] >= 2, f"SOCKS5 с логином: запросы к Telegram идут через прокси ({SEEN})")
ok("s3cret" not in json.dumps(pub) and pub["proxy"].startswith("socks5://anna:•••@"), f"пароль прокси в студию не уходит ({pub['proxy']})")
saved = json.loads((tmp / "cfg/telegram.json").read_text())
ok(saved.get("proxy", "").endswith(f"@127.0.0.1:{socks_port}"), "прокси сохранён в настройках Telegram")

try:
    tg.set_proxy("socks5://anna:wrong@127.0.0.1:%d" % socks_port); bad = ""
except TelegramError as e:
    bad = str(e)
ok(bad and json.loads((tmp / "cfg/telegram.json").read_text())["proxy"] == saved["proxy"],
   f"неверный пароль прокси — ошибка, прежний прокси остаётся ({bad[:70]}…)")

tg.stop()
SEEN.update(tg=0, socks=0, http=0)
tg.set_proxy(f"http://127.0.0.1:{http_port}")
tg._call("sendMessage", chat_id=1, text="привет")
ok(SEEN["http"] >= 1 and SEEN["socks"] == 0, f"HTTP-прокси тоже работает ({SEEN})")

tg.forget()
ok(json.loads((tmp / "cfg/telegram.json").read_text()) == {"proxy": f"http://127.0.0.1:{http_port}"} and not tg.connected(),
   "бот отключён — прокси остался для следующего подключения")
tg.connect(TOKEN, API)
tg.stop()
SEEN.update(http=0)
tg._call("getMe")
ok(SEEN["http"] >= 1, "новый бот сразу ходит через сохранённый прокси")
tg.set_proxy("")
tg.stop()
SEEN.update(tg=0, http=0, socks=0)
tg._call("getMe")
ok(SEEN["tg"] == 1 and SEEN["http"] == 0 and not tg.public()["proxy"], "прокси выключен — снова напрямую")
tg.stop()
