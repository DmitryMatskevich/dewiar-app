"""Dewiar не может «положить» компьютер: у ядра и окон — свои потолки памяти (systemd), программы, открытые Лирой,
живут отдельно от ядра, голоса уступают процессор. Без настоящего systemd: systemd-run подменяется скриптом."""
import os
import subprocess
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import memcap as M  # noqa: E402

# мягкий потолок — в 0,5 ГБ от жёсткого: заранее ядро не тормозим (отчёт 11.10: фразы терялись)
for t, core, ui in ((8, (3.5, 4.0), (2.0, 2.5)), (16, (6.7, 7.2), (3.5, 4.0)), (64, (7.5, 8.0), (4.5, 5.0))):
    ok(M.caps("core", t) == core and M.caps("ui", t) == ui, f"{t} ГБ памяти: ядро {M.caps('core', t)}, окна {M.caps('ui', t)}")
os.environ["DEWIAR_FAKE_RAM_GB"] = "16"
ok(M.props("core") == ["MemoryHigh=6860M", "MemoryMax=7372M", "MemorySwapMax=1G"], f"свойства службы: {M.props('core')}")

# systemd-run есть — запуск в своей коробке; нет — как раньше
tmp = Path(tempfile.mkdtemp())
log = tmp / "runs.txt"
fake = tmp / "bin" / "systemd-run"
fake.parent.mkdir()
fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n')
fake.chmod(0o755)
(tmp / "run" / "systemd").mkdir(parents=True)
env0 = dict(os.environ)
os.environ.update(PATH=f"{fake.parent}:{os.environ['PATH']}", XDG_RUNTIME_DIR=str(tmp / "run"))
os.environ.pop("DEWIAR_NO_SCOPE", None)
M._SCOPE.clear()
cmd = M.scope_cmd(["python3", "ui/app.py"], "ui", "Dewiar — окна")
ok(cmd[:5] == ["systemd-run", "--user", "--scope", "--quiet", "--collect"] and "MemoryMax=4096M" in cmd and "MemoryHigh=3584M" in cmd
   and cmd[-2:] == ["python3", "ui/app.py"], f"окна — в коробке с потолком: {' '.join(cmd)}")
app = M.scope_cmd(["xdg-open", "https://ya.ru"])
ok(app[0] == "systemd-run" and not any(x.startswith("Memory") for x in app), "программа, открытая Лирой, — отдельно и без потолка")
ok("--scope" in log.read_text(), "возможность проверена пробным запуском")
os.environ["DEWIAR_NO_SCOPE"] = "1"
M._SCOPE.clear()
ok(M.scope_cmd(["xdg-open", "x"]) == ["xdg-open", "x"], "без systemd — запуск как раньше")
os.environ.pop("DEWIAR_NO_SCOPE")
M._SCOPE.clear()

# launch() Лиры идёт через systemd-run (и не закроется при перезапуске ядра)
os.environ.pop("DEWIAR_DRY_TOOLS", None)
import tools  # noqa: E402
log.write_text("")
from unittest.mock import patch  # noqa: E402
# This block exercises a fake Linux systemd/xdg-open, even when the host is Mac.
# Native Mac dispatch is covered separately in macos_windows_test.py.
with patch.object(tools, "MAC", False), patch.object(tools, "WIN", False):
    tools.launch(["xdg-open", "https://ya.ru"])
time.sleep(0.5)
ok("xdg-open https://ya.ru" in log.read_text() and "--scope" in log.read_text(), f"launch: {log.read_text().strip()}")

# сколько занимает сам Dewiar: ядро с потомками и окна (ui/app.py) с их WebKit
kid = subprocess.Popen([sys.executable, "-c", "import time; x = bytearray(300 * 2**20); x[::4096] = b'1' * len(x[::4096]); time.sleep(20)"])
(tmp / "ui").mkdir()
(tmp / "ui" / "app.py").write_text("import time\nx = bytearray(200 * 2**20)\nx[::4096] = b'1' * len(x[::4096])\ntime.sleep(20)\n")
ui = subprocess.Popen([sys.executable, str(tmp / "ui" / "app.py")])
time.sleep(2.5)
m = M.dewiar_memory()
ok(m["core"] >= 0.3 and m["ui"] >= 0.19 and m["biggest"][1] >= 0.29, f"замер: {m}")
ok(M.tree_gb(os.getpid()) >= 0.3, "ядро считается вместе с процессами, которые запустило")
kid.kill()
ui.kill()

# служба ядра: потолок памяти, скромная доля процессора; голоса — с пониженным приоритетом
os.environ.clear()
os.environ.update(env0)
os.environ["DEWIAR_FAKE_RAM_GB"] = "16"
setup = SourceFileLoader("dewiar_setup", str(ROOT / "packaging" / "dewiar-setup")).load_module()
u = setup.UNIT_TEXT
ok("MemoryHigh=6860M" in u and "MemoryMax=7372M" in u and "MemorySwapMax=1G" in u and "CPUWeight=50" in u
   and "IOWeight=50" in u and "Nice=5" in u, "служба ядра с потолком памяти и скромной долей процессора")
ok(setup.VERSION != "0.16.1", "версия настройки сменилась — юнит перепишется при обновлении")
src = (ROOT / "core" / "tts.py").read_text(encoding="utf-8")
ok('"preexec_fn": lambda: os.nice(5)' in src, "голоса уступают процессор (nice +5)")
app_src = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
ok("def _own_box" in app_src and "_own_box()" in app_src and 'scope_cmd([sys.executable, str(ROOT / "core/main.py")], "core"' in app_src,
   "окна и ядро из исходников — тоже в своих коробках")
ok("def _ui_too_big" in app_src and "_studio is not None" in app_src, "окна разрослись — перезапуск окон, но не при открытой студии")
print("\nИТОГ:", "всё хорошо" if not fails else f"ошибок {len(fails)}")
sys.exit(1 if fails else 0)
