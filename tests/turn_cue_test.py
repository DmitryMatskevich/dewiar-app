"""«Ваша очередь»: ответ закончен и можно говорить без имени — ядро сообщает окнам (событие turn) и звучит мягкий
сигнал; виджет пишет «Ваша очередь — говорите», а светлая полоса в надписи тает, пока ждём. Окно истекло — снова
покой. Живое ядро с моделью-заглушкой и настоящая страница виджета (ui/hud.html) в Chromium."""
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


# сигнал «ваша очередь» — свой: тише и мягче, чем «слушаю», и отличается на слух
import numpy as np  # noqa: E402
from audio_io import tone  # noqa: E402
w, t = np.frombuffer(tone("wake"), np.int16), np.frombuffer(tone("turn"), np.int16)
ok(0 < np.abs(t).max() < np.abs(w).max() * 0.8, f"«ваша очередь» тише «слушаю»: {np.abs(t).max()} < {np.abs(w).max()}")
ok(len(t) != len(w) or not np.array_equal(t, w), "и звучит по-другому")
from settings import DEFAULTS  # noqa: E402
ok(DEFAULTS["audio"]["turn_tone"] is True, "сигнал включён по умолчанию (выключается в студии)")
ok('data-k="audio.turn_tone"' in (ROOT / "ui/settings.html").read_text(encoding="utf-8"), "переключатель в студии")

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
        pg.add_init_script("window.pywebview = { api: new Proxy({}, { get: () => () => Promise.resolve(true) }) };")
        pg.goto((ROOT / "ui/hud.html").as_uri() + f"?token={tok}&port={PORT}&mode=card"); pg.wait_for_timeout(2500)
        pg.evaluate("() => { window.turns = []; core.on('turn', e => turns.push(e.seconds)); core.call('say', { text: 'Как дела?' }); }")
        seen = None
        for _ in range(60):
            pg.wait_for_timeout(200)
            if pg.evaluate("turns.length"):
                seen = pg.evaluate("() => ({ txt: document.getElementById('say').textContent, cls: document.getElementById('bubble').className, "
                                   "dur: document.getElementById('bubble').style.getPropertyValue('--turn') })")
                break
        ok(pg.evaluate("turns") == [3.0], f"ответ закончен — ядро передаёт ход: {pg.evaluate('turns')}")
        ok(seen and seen["txt"] == "Ваша очередь — говорите" and "turn" in seen["cls"] and "live" in seen["cls"],
           f"виджет: «Ваша очередь — говорите» с тающей полосой: {seen}")
        ok(seen and 2.0 <= float(seen["dur"].rstrip("s") or 0) <= 3.0, f"полоса тает столько, сколько ждём: {seen and seen['dur']}")
        pg.wait_for_timeout(1300)
        pg.screenshot(path=str(tmp / "turn.png"), clip={"x": 20, "y": 150, "width": 260, "height": 60})
        for _ in range(40):                      # окно 3 с; медленная машина проверок — ждём до 8 с
            pg.wait_for_timeout(200)
            after = pg.evaluate("() => ({ txt: document.getElementById('say').textContent, cls: document.getElementById('bubble').className })")
            if "turn" not in after["cls"]:
                break
        ok("turn" not in after["cls"] and after["txt"] != "Ваша очередь — говорите", f"окно истекло — снова покой: {after}")
        ok(not errs, f"без ошибок на странице: {errs}")
        b.close()
finally:
    core.terminate()
    mock.terminate()
    core.wait(10)
log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
ok("Traceback" not in log, "ядро без ошибок")
print("снимок:", tmp / "turn.png")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
