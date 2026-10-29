"""Отзыв на YouTube 19.10, Android 13: «Подключил всё, дал все разрешения и попросил включить блютуз, а она на вкладку
вайфай переходит». Руки искали Bluetooth в настройках пальцем и промахивались. Включить Bluetooth, Wi‑Fi, режим полёта
сам Android приложениям не даёт, поэтому теперь — инструмент phone_settings: сразу нужный экран (Bridge.openSettings),
дальше руки нажимают переключатель, а без рук ассистент честно просит нажать его самому."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


java = (ROOT / "android/src/pro/dewiar/app/Bridge.java").read_text(encoding="utf-8")
ok('"bluetooth".equals(what)) acts = new String[]{"android.settings.BLUETOOTH_SETTINGS"}' in java, "Android: bluetooth → экран Bluetooth")
ok('"wifi".equals(what)) acts = new String[]{"android.settings.panel.action.WIFI"' in java, "wifi — шторка Wi‑Fi (с запасным экраном)")
exam = (ROOT / "android/assets/www/exam.js").read_text(encoding="utf-8")
ok('id: "bluetooth"' in exam and 'tool: { phone_settings: { section: "bluetooth" } }' in exam, "сценарий экзамена «Включи блютуз»")

tmp = Path(tempfile.mkdtemp())
www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
shutil.copy(ROOT / "ui/assets/entities.js", www / "entities.js")
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(800)
        pg.evaluate("""() => { window._opened = []; D.openSettings = (w) => { window._opened.push(w); return w !== "nfc"; };
            window._hands = ""; D.handsState = () => window._hands; }""")
        spec = pg.evaluate("() => toolSpecs().find(t => t.name === 'phone_settings')")
        ok(bool(spec) and "bluetooth" in spec["parameters"]["properties"]["section"]["enum"], "инструмент phone_settings есть у модели")
        r = pg.evaluate("() => runTool('phone_settings', { section: 'bluetooth' })")
        ok(pg.evaluate("window._opened") == ["bluetooth"], f"открыт именно экран Bluetooth: {pg.evaluate('window._opened')}")
        ok("Bluetooth" in r and "нажать переключатель" in r and "Не говори «включила»" in r, f"без рук — честно попросить нажать: {r}")
        pg.evaluate("window._hands = 'on'")
        r = pg.evaluate("() => runTool('phone_settings', { section: 'wifi' })")
        ok("phone_screen" in r and "phone_tap" in r, f"с руками — дальше посмотреть экран и нажать: {r}")
        r = pg.evaluate("() => runTool('phone_settings', { section: 'nfc' })")
        ok("не открылся" in r and "Настройки → NFC" in r, f"экран не открылся — подсказка, где искать: {r}")
        r = pg.evaluate("() => runTool('phone_settings', { section: 'кофеварка' })")
        ok("Такого экрана нет" in r and pg.evaluate("window._opened.length") == 3, "неизвестный экран — ничего не открываем")
        prompt = pg.evaluate("() => systemPrompt ? systemPrompt() : ''") if pg.evaluate("typeof systemPrompt === 'function'") else ""
        if prompt:
            ok("phone_settings" in prompt, "в подсказке модели — про экраны настроек")
        web = pg.evaluate("() => WEB_NO_TOOLS.has('phone_settings')")
        ok(web, "в веб-версии (iPhone, Mac) инструмента нет")
        ok(not errs, f"без ошибок страницы: {errs[:2]}")
        b.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    raise SystemExit(1)
