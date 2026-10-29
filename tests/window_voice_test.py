"""Окно голосом: «спрячься» — виджет в трей, «покажись» — обратно, «отключись» — выйти из Dewiar совсем.
Живое ядро и настоящая страница виджета (ui/hud.html) в Chromium; окнами вместо ui/app.py управляет поддельный
pywebview.api — проверяем, что страница зовёт to_tray / from_tray / quit_all, а ядро после «отключись» завершается
и само, если интерфейс не справился. Плюс разбор фраз (window_phrase) и «имя услышано, а окна нет» (_ui_wake)."""
import json, os, socket, subprocess, sys, tempfile, time, types
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
PY = os.environ.get("DEWIAR_PY") or sys.executable
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ---------- фразы ----------
from engine_base import window_phrase  # noqa: E402
cases = {"Спрячься.": "tray", "ну спрячься, пожалуйста": "tray", "уйди в трей": "tray", "убери виджет": "tray", "а теперь скройся": "tray",
         "скрыться": "tray", "убраться с экрана": "tray", "Лира, убраться с экрана": "tray",
         "Покажись!": "show", "вернись на экран": "show", "покажи виджет": "show",
         "Отключись": "quit", "отключись совсем": "quit", "закрой программу": "quit", "выключись, пожалуйста": "quit",
         "выключи компьютер": None, "выключи звук": None, "спрячь файл в папку": None, "спрячься от дождя": None,
         "покажи погоду": None, "расскажи, как спрятаться от жары": None,
         # с именем впереди (отчёт 06.10: «Лира, спрячься» ушло модели — она долго рассказывала про трей и осталась на экране)
         "Лира, спрячься": "tray", "Агент, покажись": "show", "Джарвис отключись": "quit", "не спрячься": None, "Лира, спрячь файл": None}
bad = {t: (window_phrase(t), want) for t, want in cases.items() if window_phrase(t) != want}
ok(not bad, f"фразы разобраны верно ({len(cases)}): {bad or 'все'}")

# ---------- имя услышано, а окна нет ----------
from engine_commands import CommandsMixin  # noqa: E402
launched = []
host = types.SimpleNamespace(bridge=types.SimpleNamespace(clients=set(), seen=False), _ui_launch=lambda: launched.append(1))
CommandsMixin._ui_wake(host); time.sleep(0.2)
ok(not launched, "ядро без окон (интерфейса не было) — на имя окно не открывает")
host.bridge.seen = True
CommandsMixin._ui_wake(host); CommandsMixin._ui_wake(host); time.sleep(0.2)
ok(len(launched) == 1, f"интерфейс был и пропал — на имя открывает виджет, и не чаще раза в 30 с ({len(launched)})")
host.bridge.clients = {1}; host._ui_launched = -1e9
CommandsMixin._ui_wake(host); time.sleep(0.2)
ok(len(launched) == 1, "окно на месте — ничего не открывает")

# ---------- живое ядро + виджет ----------
tmp = Path(tempfile.mkdtemp())
LLM, PORT = free(), free()
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM)])
cfg = tmp / "cfg"; cfg.mkdir()
(cfg / "settings.json").write_text(json.dumps({"persona": "dewiar", "audio": {"sounds": False}, "ui": {"welcome": "done"},
                                               "stt": {"engine": "vosk"}, "tts": {"engine": "off"},
                                               "llm": {"active": "m", "profiles": {"m": {"name": "Мок", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "mock"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(cfg), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"))
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
for _ in range(150):
    time.sleep(0.2)
    if (cfg / "token").exists():
        break
time.sleep(2)
tok = (cfg / "token").read_text().strip()
FAKE = """window.calls = []; window.pywebview = { api: new Proxy({}, { get: (_, k) => (...a) => { window.calls.push(k); return Promise.resolve(true); } }) };"""
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 420, "height": 300}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script(FAKE)
        pg.goto((ROOT / "ui/hud.html").as_uri() + f"?token={tok}&port={PORT}&mode=card"); pg.wait_for_timeout(2500)

        def say(text, want, wait=6):
            pg.evaluate("() => { window.calls = []; }")
            pg.evaluate("(t) => core.call('say', { text: t })", text)
            for _ in range(wait * 5):
                pg.wait_for_timeout(200)
                c = pg.evaluate("window.calls")
                if want in c:
                    return c
            return pg.evaluate("window.calls")
        c = say("Спрячься", "to_tray")
        ok("to_tray" in c, f"«спрячься» — виджет уходит в трей: {c}")
        st = json.loads((cfg / "settings.json").read_text())
        ok(st.get("ui", {}).get("hud_tray") is True, "и запоминает: при следующем запуске — тоже в трее")
        c = say("покажись", "from_tray")
        ok("from_tray" in c, f"«покажись» — виджет возвращается: {c}")
        c = say("Отключись", "quit_all")
        ok("quit_all" in c, f"«отключись» — интерфейс завершает всё: {c}")
        t0 = time.monotonic()
        while core.poll() is None and time.monotonic() - t0 < 12:
            time.sleep(0.3)
        ok(core.poll() is not None, f"интерфейс не завершил ядро — ядро выходит само ({time.monotonic() - t0:.1f} с)")
        ok(core.poll() == 0, f"обычный выход (код 0: служба с Restart=on-failure не поднимет его снова): {core.poll()}")
        ok(not errs, f"без ошибок на странице: {errs}")
        b.close()
finally:
    if core.poll() is None:
        core.terminate()
    mock.terminate()
log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
ok("Отключаюсь" in log or "выхожу из Dewiar" in log, "в журнале — почему ядро вышло")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
