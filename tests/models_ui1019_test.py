"""«Слух и голос» понятнее (план 19.10): в студии видно, какие модели скачаны на этом компьютере и сколько занимают,
что ещё не скачано (и сколько весит) — с кнопкой «Скачать» заранее, без переключения; ход скачивания — в строке.
Настоящее ядро (models_status, models_get) и студия (settings.html) в Chromium."""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = sys.executable
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
PORT = free()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "lira", "stt": {"engine": "gigaam"}, "tts": {"engine": "vosk"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "ui": {"welcome": "done", "intro": "done"}}))
# Piper «Ирина» — как будто скачана (оба файла), остальные — нет
mdir = tmp / "st/models"
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_VOSK_RETRY_MIN="999", DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")   # не «слабый» — лёгкий режим не меняет выбор
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(ROOT / "tests/silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
from playwright.sync_api import sync_playwright  # noqa: E402
FAKE = "window.pywebview = { api: new Proxy({}, { get: () => () => Promise.resolve(true) }) };"
try:
    for _ in range(150):
        if (tmp / "cfg/token").exists():
            break
        time.sleep(0.2)
    time.sleep(2)
    tok = (tmp / "cfg/token").read_text().strip()
    with sync_playwright() as p:
        b = p.chromium.launch(); errs = []
        pg = b.new_page(viewport={"width": 1280, "height": 1200})
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script(FAKE)
        pg.goto((ROOT / "ui/settings.html").as_uri() + f"?port={PORT}&token={tok}"); pg.wait_for_timeout(2500)
        d = pg.evaluate("core.call('models_status')")
        keys = [x["key"] for x in d["items"]]
        ok({"names", "gigaam", "vosk_tts", "piper:ru_RU-irina-medium", "piper:ru_RU-denis-medium"} <= set(keys),
           f"ядро знает все модели слуха и голоса: {keys}")
        g = next(x for x in d["items"] if x["key"] == "gigaam")
        ok(g["group"] == "ears" and g["used"] is True and isinstance(g["mb"], int) and g["mb"] > 0, f"GigaAM: выбран — «используется», размер известен: {g}")
        bad = pg.evaluate("core.call('models_get', { key: '../../etc' }).then(() => 'ok', e => e.message)")
        ok(bad != "ok" and "модели" in bad, f"скачать можно только известную модель: {bad}")
        pg.evaluate("$('nav button[data-p=\"ears\"]').click()"); pg.wait_for_timeout(900)
        E = pg.inner_text("#mdl-ears")
        ok("GigaAM v3" in E and "Слух имени" in E and ("скачана" in E), f"«Слух» → «Скачано на этом компьютере»: {E[:160]!r}")
        ok(("Скачать" in E) == any(not x["installed"] for x in d["items"] if x["group"] == "ears"),   # любое не скачанное в «Слухе», не только GigaAM
           "не скачанное — с кнопкой «Скачать»")
        pg.evaluate("$('nav button[data-p=\"voice\"]').click()"); pg.wait_for_timeout(900)
        V = pg.inner_text("#mdl-voice")
        ok("Vosk TTS" in V and "Piper — Ирина" in V and "~63 МБ" in V, f"«Голос»: Vosk TTS и голоса Piper с размерами: {V[:200]!r}")
        # ход скачивания: строка показывает процент, когда ядро шлёт downloads «m:<ключ>»
        pg.evaluate("""() => { renderStatus(Object.assign({}, lastStatus, { downloads: { 'm:piper:ru_RU-denis-medium': 0.42 } })); }""")
        V2 = pg.inner_text("#mdl-voice")
        ok("скачивается 42%" in V2, "ход скачивания — процентом в строке модели")
        ok(not errs, f"без ошибок: {errs[:2]}")
        b.close()
finally:
    core.terminate()
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
