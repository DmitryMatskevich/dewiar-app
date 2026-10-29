"""20.10, 22:41: «4 раза просил Лиру спрятаться — исчезала, говорила „ушла в тишину“ и тут же появлялась в ожидании моего
ответа». Распознаватель слышал «Спрякся» — быстрый путь не узнавал, модель понимала и прятала виджет (widget), а после
ответа ядро открывало «Ваша очередь» — состояние «слушаю» возвращало виджет на экран. Теперь: ход, в котором виджет
спрятали, не ждёт ответа; и «спрякся», «спрячся» узнаются сразу, без модели."""
import json, os, socket, subprocess, sys, tempfile, time
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




from engine_base import window_phrase  # noqa: E402
ok([window_phrase(t) for t in ["Спрякся.", "Лира, спрячся", "спрячтесь", "спрячь файл", "не спрячься"]] == ["tray", "tray", "tray", None, None],
   "«Спрякся», «Лира, спрячся» — сразу «в трей»; «спрячь файл», «не спрячься» — нет")

tmp = Path(tempfile.mkdtemp())
LLM, PORT = free(), free()
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM)])
cfg = tmp / "cfg"; cfg.mkdir()
(cfg / "settings.json").write_text(json.dumps({"persona": "dewiar", "audio": {"sounds": True, "follow_up_seconds": 3},
                                               "ui": {"news_seen": "99.0.0", "welcome": "done"}, "stt": {"engine": "vosk"}, "tts": {"engine": "off"},
                                               "llm": {"active": "m", "profiles": {"m": {"name": "Мок", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "mock"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(cfg), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_LOG_LEVEL="DEBUG")
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
for _ in range(150):
    time.sleep(0.2)
    if (cfg / "token").exists():
        break
time.sleep(2)
tok = (cfg / "token").read_text().strip()
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 300, "height": 420}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script("window.pywebview = { api: new Proxy({}, { get: (_, k) => (...a) => { (window.apiCalls = window.apiCalls || []).push(k); return Promise.resolve(true); } }) };")
        pg.goto((ROOT / "ui/hud.html").as_uri() + f"?token={tok}&port={PORT}&mode=card"); pg.wait_for_timeout(2500)
        pg.evaluate("() => { window.ev = []; ['turn', 'window', 'state'].forEach(k => core.on(k, e => ev.push([k, e.do || e.state || e.seconds]))); window.apiCalls = []; core.call('say', { text: 'Уйди в тень' }); }")
        for _ in range(50):
            pg.wait_for_timeout(200)
            if any(e[0] == "window" for e in pg.evaluate("ev")):
                break
        pg.wait_for_timeout(2500)
        ev, calls = pg.evaluate("ev"), pg.evaluate("apiCalls")
        ok(["window", "tray"] in ev, f"модель спрятала виджет: {ev}")
        ok(not any(e[0] == "turn" for e in ev) and ["state", "listening"] not in ev[ev.index(["window", "tray"]):] if ["window", "tray"] in ev else False,
           f"после — без «Ваша очередь» и «слушаю»: {ev}")
        ok("to_tray" in calls and "show" not in calls[calls.index("to_tray"):] if "to_tray" in calls else False, f"и виджет не вернулся на экран: {calls}")
        ok(not errs, f"без ошибок на странице: {errs}")
        b.close()
finally:
    core.terminate()
    mock.terminate()
    core.wait(10)
log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
ok("Traceback" not in log, "ядро без ошибок")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
