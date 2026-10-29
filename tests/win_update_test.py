"""Обновления для двух ОС из одного latest.json: publish_update кладёт .deb и .exe, Linux видит свой пакет,
Windows — свой раздел "windows"; контрольная сумма проверяется; старый latest.json без Windows не ломает клиентов.
На Linux Windows-клиент изображается подменой updater.WIN; под Windows (Wine) — настоящий."""
import http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
SRC = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg")
sys.path.insert(0, str(ROOT / "core"))
import updater  # noqa: E402
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)

site = tmp / "www" / "dwrapp"; site.mkdir(parents=True)
deb = tmp / "dewiar-assistant_99.2.0_all.deb"; deb.write_bytes(b"!<arch>\n" + os.urandom(4000))
exe = tmp / "Dewiar-Setup-99.3.0.exe"; exe.write_bytes(b"MZ" + os.urandom(6000))
pub = lambda *a: subprocess.run([sys.executable, str(SRC / "tools/publish_update.py"), *map(str, a)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
if os.name == "nt":      # publish_update — инструмент сервера (Linux); под Wine shutil.copy2 не работает (нет CopyFile2)
    import hashlib
    sha = lambda f: hashlib.sha256(f.read_bytes()).hexdigest()
    shutil.copyfile(deb, site / deb.name); shutil.copyfile(exe, site / exe.name)
    base = {"version": "99.2.0", "deb": deb.name, "sha256": sha(deb), "size": deb.stat().st_size, "notes": "Linux"}
    (site / "latest.json").write_text(json.dumps(base), encoding="utf-8")
    pub = lambda *a: (lambda m: (m.update(windows={"version": "99.3.0", "exe": exe.name, "sha256": sha(exe), "size": exe.stat().st_size}),
                                 (site / "latest.json").write_text(json.dumps(m), encoding="utf-8"),
                                 subprocess.CompletedProcess([], 0, "", ""))[-1])(json.loads((site / "latest.json").read_text(encoding="utf-8")))
    r = subprocess.CompletedProcess([], 0, "", "")
else:
    r = pub(deb, site, "--notes", "Linux")
man = json.loads((site / "latest.json").read_text(encoding="utf-8"))
ok(r.returncode == 0 and man.get("deb") == deb.name and "windows" not in man, f"сначала только .deb ({r.stderr.strip()[-120:]})")
r = pub(exe, site, "--notes", "Windows")
man = json.loads((site / "latest.json").read_text(encoding="utf-8"))
w = man.get("windows") or {}
ok(r.returncode == 0 and w.get("exe") == exe.name and w.get("version") == "99.3.0" and len(w.get("sha256", "")) == 64
   and (site / exe.name).exists(), "установщик Windows — свой раздел в latest.json")
ok(man.get("deb") == deb.name and man.get("version") == "99.2.0", "раздел Linux не тронут")

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
H = type("H", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), lambda *a, **k: H(*a, directory=str(tmp / "www"), **k))
threading.Thread(target=srv.serve_forever, daemon=True).start()


class S:
    def get(self, *k):
        return {"updates": {"url": f"http://127.0.0.1:{port}/dwrapp/latest.json"}}.get(k[0], {}).get(k[1]) if len(k) > 1 else None


def client(win, name):
    updater.WIN = win
    return updater.Updater(S(), "0.12.0", tmp / name, emit=lambda *a, **k: None)


real_win = os.name == "nt"
u = client(True, "st_win")
i = u.check()
ok(i and i["version"] == "99.3.0" and Path(i["path"]).name == exe.name and Path(i["path"]).read_bytes() == exe.read_bytes(),
   f"Windows-клиент скачал свой установщик{' (настоящий Windows)' if real_win else ''}")
u = client(False, "st_lin")
i = u.check()
ok(i and i["version"] == "99.2.0" and i["path"].endswith(".deb"), "Linux-клиент скачал .deb, Windows-раздел не видит")

man["windows"]["sha256"] = "0" * 64
(site / "latest.json").write_text(json.dumps(man), encoding="utf-8")
u = client(True, "st_win2")
ok(u.check() is None and "контрольная" in u.last.get("error", ""), "подменённый установщик не принимается")

del man["windows"]
(site / "latest.json").write_text(json.dumps(man), encoding="utf-8")
ok(client(True, "st_win3").check() is None, "старый latest.json без Windows — Windows-клиент просто не видит обновления")
man["windows"] = {"version": "0.11.0", "exe": exe.name, "sha256": w["sha256"]}
(site / "latest.json").write_text(json.dumps(man), encoding="utf-8")
ok(client(True, "st_win4").check() is None, "версия на сайте старше — не предлагаем")
srv.shutdown()
updater.WIN = real_win
