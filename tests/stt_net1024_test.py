"""24.10, владелец (телефон, мобильная сеть): «Не получилось услышать: нет сети для распознавания» — через раз. Распознавание
телефона (Google) работает через интернет и на слабой сети обрывается. Теперь сбой сети — не ошибка: есть распознавание
Dewiar — фразу слушает сервер (два сбоя за 10 минут — сервер слушает 10 минут); нет — один тихий повтор «скажите ещё раз»."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


tmp = Path(tempfile.mkdtemp()); www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
SETUP = """(ears) => { window._srv = []; window._sys = 0; window._toasts = [];
  D.recognizer = () => true; D.listen = () => { window._sys++; }; D.listenServer = (g, u) => { window._srv.push(u); };
  toast = (t) => window._toasts.push(t); needPerm = async () => true;
  cloudEars = () => ears ? { url: 'https://dewiar.pro/dwrapp/cloud.php?p=', key: 'k' } : null;
  sttNetUntil = 0; sttNetFails = []; sttNetRetryAt = 0; S.stt.engine = 'system'; }"""
NETERR = "async () => { await listen(); sttStarted = true; N.ev({ type: 'stt_error', g: sttGen, code: 2, msg: 'нет сети для распознавания' }); }"
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
        # есть распознавание Dewiar
        pg.evaluate(SETUP, True)
        pg.evaluate(NETERR); pg.wait_for_timeout(900)
        st = pg.evaluate("() => ({ srv: window._srv.length, toasts: window._toasts, bot: $('#bot').textContent, until: sttNetUntil })")
        ok(st["srv"] == 1 and "сервер Dewiar" in " ".join(st["toasts"]) and "Не получилось" not in st["bot"],
           f"сбой сети — эту фразу слушает сервер Dewiar, без ошибки на экране: {st}")
        ok(st["until"] == 0, "один сбой — сервер только для этой фразы")
        pg.evaluate("() => { window._srv = []; sttVia = 'system'; }")
        pg.evaluate(NETERR); pg.wait_for_timeout(900)
        until = pg.evaluate("() => sttNetUntil - Date.now()")
        ok(500e3 < until <= 600e3, f"второй сбой за 10 минут — сервер слушает 10 минут ({until / 1000:.0f} с)")
        pg.evaluate("async () => { window._srv = []; window._sys = 0; await listen(); }"); pg.wait_for_timeout(200)
        ok(pg.evaluate("() => window._srv.length") == 1 and pg.evaluate("() => window._sys") == 0, "следующее «слушаю» — сразу сервер")
        # распознавания Dewiar нет
        pg.evaluate(SETUP, False)
        pg.evaluate(NETERR); pg.wait_for_timeout(900)
        st = pg.evaluate("() => ({ sys: window._sys, toasts: window._toasts, bot: $('#bot').textContent })")
        ok(st["sys"] == 2 and "скажите ещё раз" in " ".join(st["toasts"]).lower() and "Не получилось" not in st["bot"],
           f"без сервера — один тихий повтор: {st}")
        pg.evaluate(NETERR); pg.wait_for_timeout(900)
        bot = pg.evaluate("() => $('#bot').textContent")
        ok("нет сети для распознавания" in bot and "Связь слабая" in bot, f"второй подряд — честно, что связь слабая: {bot}")
        # №229/№223: «Software caused connection abort» — понятной фразой; 127.0.0.1 на телефоне — объяснить, что это сам телефон
        r = pg.evaluate("() => [netErr('java.net.SocketException: Software caused connection abort', 'https://dewiar.pro/dwrapp/cloud.php?p='), netErr('Failed to connect to /127.0.0.1:1234', 'http://127.0.0.1:1234/v1')]")
        ok(r[0] == "связь с сервером оборвалась" and "сам телефон" in r[1], f"сетевые ошибки — по-человечески: {r}")
        ok(not errs, f"без ошибок: {errs[:2]}")
        b.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    raise SystemExit(1)
