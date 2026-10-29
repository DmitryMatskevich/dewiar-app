"""«Близкие» на компьютере не ломаются от чужих данных: кривое сообщение не стопорит ящик, имя собеседника «..»
не уводит файлы из папки «Близкие», текст сообщений не попадает в журнал, брошенный звонок не держит частый опрос,
файлы близким — только с подтверждением, ключи и пароли не уходят вовсе. Без сервера: запросы подменены."""
import logging, os, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
os.environ.setdefault("DEWIAR_CONFIG_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(ROOT / "core"))
import closecrypto as CC  # noqa: E402
import close as C  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


class R:
    def __init__(s, d, content=b""):
        s.d, s.content = d, content

    def json(s):
        return s.d


tmp = Path(tempfile.mkdtemp())
said = []


class Host:
    s = type("S", (), {"get": lambda self, *a: None})()
    workspace = type("W", (), {"root": str(tmp / "ws")})()
    def credentials(self): return ("tok", "tok", "u")
    def emit(self, *a, **k): pass
    def push(self, *a, **k): pass
    def announce(self, t): said.append(t)
    def device_name(self): return "x"


c = C.Close(Host(), tmp / "close.json", tmp / "close.key")
apriv, apub = CC.keypair()
priv, pub = c._keys()
c.st["people"] = [{"pid": "A", "name": "..", "devs": [{"dev": "da", "pub": CC.b64e(apub)}]}]
c.st["me"] = {"devs": []}
c.st["hello_at"] = time.time()
to_me = {c.st["dev"]: CC.b64d(pub)}
fk = os.urandom(32)
blob = CC.seal(fk, b"secret notes")
good = CC.encrypt_for({"t": "привет", "ts": 1, "mid": "m2", "f": [{"id": "f1", "name": "../notes.md", "size": 12, "key": CC.b64e(fk)}]}, apriv, "da", to_me)
inbox = {"items": [{"id": 1, "peer": "A", "name": "..", "env": CC.encrypt_for(["не", "словарь"], apriv, "da", to_me)},
                   {"id": 2, "peer": "A", "name": "..", "env": CC.encrypt_for({"t": "x", "ts": "abc", "mid": "m3"}, apriv, "da", to_me)},
                   {"id": 3, "peer": "A", "name": "..", "kind": "sig", "env": CC.encrypt_for({"s": {"type": "ring", "ts": "zz"}}, apriv, "da", to_me)},
                   {"id": 4, "peer": "A", "name": "..", "env": good}], "last": 4}


def req(m, p, body=None, raw=None, params=None, timeout=20):
    if p == "/close/file":
        return R({}, blob)
    return R(inbox)


c._req = req
try:
    n = c.poll(); err = ""
except Exception as e:  # noqa: BLE001
    n, err = -1, repr(e)
ok(n == 2 and c.st["cursor"] == 4, f"кривые сообщения пропущены, остальные приняты, курсор сдвинут: новых {n}, курсор {c.st['cursor']} {err}")
for _ in range(50):
    if any((tmp / "ws").rglob("notes.md")):
        break
    time.sleep(0.1)
got = [str(x.relative_to(tmp / "ws")) for x in (tmp / "ws").rglob("*") if x.is_file()]
ok(got and all(g.startswith("Близкие" + os.sep) for g in got), f"имя «..» и файл «../notes.md» — всё внутри «Близкие»: {got}")
ok(C.safe_name("CON") == "без имени" and C.safe_name(" ..мама.. ") == "мама" and "/" not in C.safe_name("a/b"), "имена из чужих данных чистятся (Windows-запретные, точки, разделители)")

# журнал: только длина, без текста
logs = []
h = logging.Handler(); h.emit = lambda r: logs.append(r.getMessage()); logging.getLogger("dewiar.close").addHandler(h); logging.getLogger("dewiar.close").setLevel(logging.INFO)
c.st["people"][0]["name"] = "Анна"
sent = []
c._req = lambda m, p, body=None, raw=None, params=None, timeout=20: (sent.append(p), R({"ok": True}))[1]
c.send("Анна", "мой пароль от домофона 4521")
ok(sent and not any("4521" in x for x in logs), f"текст сообщения не попадает в журнал: {[x for x in logs if '💌' in x]}")

# брошенный звонок: страница не напоминает о себе — частый опрос прекращается
now = time.time()
c.calls = {"aaaaaaaaaaaa": {"call": "aaaaaaaaaaaa", "fast": True, "at": now - 200}}
ok(not c._fast(), "закрыли окно звонка — через 90 с опрос снова обычный")
c.calls["aaaaaaaaaaaa"]["seen"] = now - 10
ok(c._fast(), "окно звонка живо — опрос раз в секунду")
c.st["invite"] = {"expires": "завтра"}
ok(c.active() in (True, False), "кривой срок приглашения не роняет фоновый опрос")

# файлы близким
import tools  # noqa: E402
tb = tools.Toolbox.__new__(tools.Toolbox); tb.hooks = {}
g1 = tb.guard("close_send", {"to": "маме", "text": "x", "files": ["~/.ssh/id_rsa"]})
g2 = tb.guard("close_send", {"to": "маме", "text": "x", "files": [str(tmp / "фото.jpg")]})
g3 = tb.guard("close_send", {"to": "маме", "text": "x"})
ok(g1[0] and not g2[0] and g2[1] and "фото.jpg" in g2[2] and g3 == (None, False, None),
   f"файл близким — с подтверждением, ключи не отправляются, просто текст — без вопроса: {g1[0]!r} / {g2[2]!r}")

print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
