"""dewiar-server setup (packaging/dewiar-server): настройка ядра на сервере без окна — модель и синхронизация пишутся
в настройки пользователя службы, служба systemd с --server. Без root: подменяем runuser/systemctl и HOME."""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []
def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)

src = ROOT / "packaging/dewiar-server"
ld = importlib.machinery.SourceFileLoader("dewiar_server", str(src))
spec = importlib.util.spec_from_loader("dewiar_server", ld)
M = importlib.util.module_from_spec(spec); ld.exec_module(M)
tmp = Path(tempfile.mkdtemp())
M.HOME, M.UNIT, M.APP, M.PY = tmp / "home", tmp / "dewiar-server.service", ROOT, Path(sys.executable)
calls, real = [], subprocess.run


def fake_run(cmd, *a, **k):
    calls.append(cmd)
    if cmd[:2] == ["runuser", "-u"]:                     # выполняем код настройки как есть, но от себя и с HOME во временной папке
        i = cmd.index("env")
        return real([cmd[i + 2], *cmd[i + 3:]], *a, env=dict(os.environ, HOME=str(M.HOME)), **k)
    return subprocess.CompletedProcess(cmd, 0, "", "")


M.subprocess.run = fake_run
os.environ.update(DEWIAR_LLM_URL="http://127.0.0.1:11434/v1/", DEWIAR_LLM_MODEL="qwen3:8b", DEWIAR_LLM_KEY="", DEWIAR_LOGIN="")
M.HOME.mkdir(parents=True)
M.configure()
cfg = json.loads((M.HOME / ".config/dewiar/settings.json").read_text(encoding="utf-8"))
prof = cfg["llm"]["profiles"].get(cfg["llm"]["active"]) or {}
ok(prof.get("base_url") == "http://127.0.0.1:11434/v1" and prof.get("model") == "qwen3:8b", f"модель записана: {prof}")
ok(cfg["sync"]["enabled"] is True, "синхронизация включена — телефон увидит сервер")
M.write_unit()
unit = M.UNIT.read_text(encoding="utf-8")
ok("--server" in unit and "User=dewiar-assistant" in unit and "Restart=on-failure" in unit and "DEWIAR_PORT=8775" in unit,
   "служба: --server, свой пользователь, перезапуск при сбое, свой порт")
ok(["systemctl", "enable", "--now", "dewiar-server.service"] in calls, "служба включена и запущена")
r = real([sys.executable, str(src), "help"], capture_output=True, text=True)
ok(r.returncode == 0 and "sudo dewiar-server setup" in r.stdout, "dewiar-server help")
print("ALL OK" if not fails else f"ошибок: {len(fails)}")
sys.exit(1 if fails else 0)
