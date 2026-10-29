"""Тестировщик на Windows, 14.10:
1. «Нажал „Обновить“ — программа не перезапустилась сама, пришлось запускать». Установщик запускает новую версию
   сам (NSIS LaunchApp), а на случай, если не вышло, — сторож PowerShell: ждёт конца установщика, 10 с, и если Dewiar
   из папки установки не запущен — запускает (пробелы, кириллица и апостроф в пути не мешают).
2. «Цветной кружок у выбора персонажа читается как „в сети / не в сети“». Вместо кружка — контурный силуэт героини
   (длинные волосы) или героя (короткая стрижка) в стиле остальных значков; лицо в 20 px было «пятном» (14.10)."""
import base64, json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
PY = os.environ.get("DEWIAR_PY") or sys.executable
sys.path.insert(0, str(ROOT / "ui"))
import app as A  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ── 1. сторож обновления (с 0.38.91 — cmd, не скрытый PowerShell: антивирусы видели в нём вирус) ──
sc = A.relaunch_script(4242)
# №225 (24.10): tasklist Kaspersky останавливал вопросом — сторож ждёт метку update.run, которую убирает установщик
ok(sc.isascii() and "tasklist" not in sc and 'if exist "%DW_FLAG%" goto wait' in sc and "if %N% GEQ 200 goto done" in sc
   and "ping -n 11 127.0.0.1" in sc, "ждёт конца установщика (метка, не список процессов; не дольше ~10 мин) и ещё 10 с")
ok('set /p P0=<"%DW_PID%"' in sc and 'set /p P=<"%DW_PID%"' in sc and 'if defined P if not "%P%"=="%P0%" goto end' in sc,
   "запускает, только если Dewiar так и не поднялся (номер в ui.pid не сменился)")
nsi = (ROOT / "packaging/windows/dewiar.nsi").read_text(encoding="utf-8")
ok(nsi.count('Delete "$LOCALAPPDATA\\Dewiar\\update.run"') == 2 and "Function .onInstFailed" in nsi, "установщик убирает метку в конце и при сбое")
ok('start "" "%DW_EXE%" -X utf8 "%DW_APP%"' in sc and 'del "%~f0"' in sc, "пути — переменными окружения (кириллица не мешает), сценарий удаляет себя")
src = (ROOT / "ui/app.py").read_text(encoding="utf-8")
ok("relaunch_after(inst.pid," in src and '["cmd.exe", "/d", "/c", str(f)]' in src and "EncodedCommand" not in src.split("def relaunch_after")[1][:900],
   "«Обновить» на Windows — установщик и сторож (cmd без окна)")
ok('DW_FLAG=str(STATE_DIR / "update.run")' in src and '(STATE_DIR / "update.run").write_text' in src, "метку кладёт программа перед установщиком")

# ── 2. лицо персонажа вместо кружка ──
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
PORT = free()
(tmp / "cfg/settings.json").write_text(json.dumps({"persona": "lira", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False},
                                                    "ui": {"intro": "done", "welcome": "done", "news_seen": "99.0.0"}, "report": {"pulse": False}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
for _ in range(150):
    time.sleep(0.2)
    if (tmp / "cfg/token").exists():
        break
time.sleep(2.5)
errs = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); hw = b.new_page(viewport={"width": 300, "height": 420}, device_scale_factor=2)
        hw.on("pageerror", lambda x: errs.append(str(x)))
        hw.goto((ROOT / "ui/hud.html").as_uri() + f"?token={(tmp / 'cfg/token').read_text().strip()}&port={PORT}&mode=card"); hw.wait_for_timeout(2500)
        T = hw.evaluate("""() => { const d = document.getElementById('team-dot');
            return { g: d.dataset.g, svg: !!d.querySelector('svg circle'), bg: d.style.backgroundImage, who: current && current.name, glow: getComputedStyle(d).boxShadow }; }""")
        ok(T["who"] == "Лира" and T["g"] == "female" and T["svg"] and not T["bg"] and T["glow"] in ("none", ""),
           f"у кнопки персонажа — контурный силуэт героини (не цветной кружок, не крошечное фото): {T}")
        hw.click("#b-team"); hw.wait_for_timeout(400)
        M = hw.evaluate("""() => [...document.querySelectorAll('#team button')].map(b => [b.querySelector('b').textContent, b.querySelector('i').dataset.g])""")
        ok(["Лира", "female"] in M and ["Патрик", "male"] in M, f"в списке — силуэты по полу: {M}")
        hw.evaluate("() => paintAv(document.getElementById('team-dot'), { name: 'Нова' })")
        ok(hw.evaluate("() => document.getElementById('team-dot').dataset.g") == "none", "пол не указан — просто силуэт")
        hw.evaluate("() => paintAv(document.getElementById('team-dot'), current)")
        hw.screenshot(path=os.environ.get("TEAM_SHOT") or str(tmp / "team.png"))
        b.close()
finally:
    core.terminate()
ok(not errs, "без ошибок JavaScript" + (f": {errs}" if errs else ""))
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
