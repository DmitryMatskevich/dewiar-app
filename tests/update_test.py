"""Обновления с сайта: publish_update.py готовит папку, ядро находит новую версию, скачивает и сверяет sha256."""
import functools, json, os, subprocess, sys, tempfile, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
SRC = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ.update(DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_CONFIG_DIR=str(tmp / "cfg"))
sys.path.insert(0, str(ROOT / "core"))
from updater import Updater, vtuple  # noqa: E402

def ok(c, m): print(("✅ " if c else "❌ ") + m)

site = tmp / "www" / "dwr"
deb = tmp / "dewiar-assistant_99.1.0_all.deb"; deb.write_bytes(b"!<arch>\n" + os.urandom(5000))
model = tmp / "vosk-model-tts-ru-0.9-multi.zip"; model.write_bytes(b"PK")
r = subprocess.run([sys.executable, str(SRC / "tools/publish_update.py"), str(deb), str(site), "--notes", "**Новое:** голоса", "--model", str(model)],
                   capture_output=True, text=True)
man = json.loads((site / "latest.json").read_text())
ok(r.returncode == 0 and man["version"] == "99.1.0" and len(man["sha256"]) == 64 and (site / deb.name).exists(), "publish_update: пакет и latest.json в папке сайта")
ok(man["mirrors"].get("vosk_tts") == "models/vosk-model-tts-ru-0.9-multi.zip" and (site / "models" / model.name).exists(), "зеркало модели голосов")
srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(tmp / "www")))
SimpleHTTPRequestHandler.log_message = lambda *a: None
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/dwr/latest.json"
class S:
    def get(self, *k): return {"url": url, "auto_check": True} if k == ("updates",) else (url if k == ("updates", "url") else None)
events = []
u = Updater(S(), "0.11.0", tmp / "st", emit=lambda *a, **k: events.append((a, k)))
info = u.check()
ok(info and info["version"] == "99.1.0" and Path(info["path"]).read_bytes() == deb.read_bytes(), "новая версия найдена и скачана")
ok(events and events[0][0][0] == "update" and u.public()["available"]["notes"].startswith("**Новое"), "интерфейсу сообщено, с «что нового»")
ok(u.mirrors.get("vosk_tts", "").endswith("/dwr/models/vosk-model-tts-ru-0.9-multi.zip"), "адрес зеркала голосов — полный")
(site / deb.name).write_bytes("!<arch>\nподмена".encode())
for f in (tmp / "st" / "updates").glob("*"): f.unlink()
ok(u.check() is None and "контрольная сумма" in u.public()["error"], "подменённый пакет не принимается")
u2 = Updater(S(), "100.0", tmp / "st")
ok(u2.check() is None and not u2.public()["error"], "своя версия новее — обновления нет")
class Bad:
    def get(self, *k): return f"http://127.0.0.1:{srv.server_port}/nope.json" if k == ("updates", "url") else None
u3 = Updater(Bad(), "0.1", tmp / "st")
ok(u3.check() is None and u3.public()["error"], "сайт недоступен — тихо, без падения")
ok(vtuple("0.10.10") > vtuple("0.10.9") and vtuple("0.11.0") > vtuple("0.10.10"), "сравнение версий 0.10.10 > 0.10.9")
