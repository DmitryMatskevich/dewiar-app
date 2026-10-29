"""Оформление 20.10 (просьбы владельца):
1. «Небесный» — стиль по умолчанию (компьютер и телефон); прежнее значение по умолчанию «Фирменная» переводится один раз
   (skin_v2), выбранную потом — не трогаем; сменить можно в «Оформлении».
2. Студия открывалась сначала фирменным синим фоном и только потом перекрашивалась: тема теперь в адресе (&skin=) и
   ставится ещё в <head>, окно — сразу цвета темы; телефон — класс темы на <body> до первой отрисовки (look_body).
3. Виджет в светлом неоморфизме сливался со светлой студией — к двум теням материала добавлена мягкая общая тень.
4. «Небесный вечер» — тёмная пара «Небесного» с тем же голубым акцентом."""
import json, os, re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_STATE_DIR"] = str(tmp / "st")
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import settings as ST  # noqa: E402
ok(ST.DEFAULTS["ui"]["skin"] == "neusky", "по умолчанию — «Небесный»")
for raw, want, why in (({"ui": {"skin": "brand"}}, "neusky", "прежнее значение по умолчанию — переводим"),
                       ({"ui": {}}, "neusky", "тема не выбиралась — «Небесный»"),
                       ({"ui": {"skin": "brand", "skin_v2": True}}, "brand", "«Фирменная», выбранная после перевода, — остаётся"),
                       ({"ui": {"skin": "neon"}}, "neon", "выбранная тема — не трогаем")):
    p = tmp / "s.json"; p.write_text(json.dumps(raw), encoding="utf-8")
    s = ST.Settings(p)
    ok(s.get("ui", "skin") == want and s.get("ui", "skin_v2") is True, f"{why}: {raw['ui']} → {s.get('ui', 'skin')}")

html = (ROOT / "ui/settings.html").read_text(encoding="utf-8")
head = html.split("</head>")[0]
ok('src="assets/skins.js"' in head and 'src="studio/theme.js"' in head and html.count("studio/theme.js") == 1, "студия: тема ставится ещё в <head>")
app = (ROOT / "ui/app.py").read_text(encoding="utf-8")
ok('"&skin=" + urllib.parse.quote(skin)' in app and '"#e3eaf4" if light else "#0a0a0f"' in app, "окно студии: тема в адресе, фон окна — цвета темы")
sys.path.insert(0, str(ROOT / "ui"))
src = re.search(r"def ui_skin\(\):.*?return sk, light\n", app, re.S).group(0)
ns = {"re": re, "HERE": ROOT / "ui"}
for raw, want in (({"skin": "brand"}, ("neusky", True)), ({"skin": "brand", "skin_v2": True}, ("brand", False)), ({"skin": "neudusk"}, ("neudusk", False)), ({}, ("neusky", True))):
    ns["ui_settings"] = lambda raw=raw: raw
    exec(src, ns)
    ok(ns["ui_skin"]() == want, f"ui_skin {raw} → {want}")

sk = (ROOT / "ui/assets/skins.js").read_text(encoding="utf-8"); css = (ROOT / "ui/assets/skins.css").read_text(encoding="utf-8")
ok('id: "neudusk", name: "Небесный вечер"' in sk and ".skin-neudusk {" in css, "«Небесный вечер» — в списке тем и в стилях")
hud = (ROOT / "ui/hud.html").read_text(encoding="utf-8")
ok("html.tw body.light:not(.mini) #card { inset" not in hud and "body.light:not(.mini) #card { box-shadow: inset 0 0 0 1px rgba(20,32,60,.075)" in hud
   and "rgba(24,38,70" not in hud and "body.neu.light { --glass-shadow" not in hud,
   "светлые темы: линия по краю карточки во всё окно — без пустого края, который Ubuntu закрашивал тёмным; тени внутри — как были")

from playwright.sync_api import sync_playwright  # noqa: E402
FAKE = "window.pywebview = { api: new Proxy({}, { get: () => () => Promise.resolve(true) }) };"
FIRST = """window._first = null; new MutationObserver((m, o) => { if (document.body && !window._first) { window._first = [document.documentElement.className,
  document.documentElement.dataset.skin || "", document.body.className]; } }).observe(document, { childList: true, subtree: true });"""
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 1100, "height": 700}); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script(FAKE); pg.add_init_script(FIRST)
    pg.goto((ROOT / "ui/settings.html").as_uri() + "?port=1&skin=neusky"); pg.wait_for_timeout(600)
    f = pg.evaluate("_first")
    ok(f and "st-light" in f[0] and "st-neu" in f[1 - 1] and f[1] == "neusky", f"студия «Небесный» — тема стоит уже до <body>: {f}")
    pg.goto((ROOT / "ui/settings.html").as_uri() + "?port=1&skin=neudusk"); pg.wait_for_timeout(600)
    br = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--brand')")
    ok("#6a8dff" in br.lower() or "106, 141, 255" in br, f"«Небесный вечер» в студии — тот же голубой акцент: {br[:60]}")
    pg.close()
    # телефон: тема на <body> до app.js
    www = tmp / "www"
    subprocess.run(["bash", str(ROOT / "android/tools/www_assemble.sh"), str(www)], check=True, capture_output=True)
    ctx = b.new_context(viewport={"width": 393, "height": 820}); ph = ctx.new_page(); ph.on("pageerror", lambda e: errs.append(str(e)))
    ph.route("https://**", lambda r: r.abort())
    ph.add_init_script("window._bodyAt = null; document.addEventListener('readystatechange', () => {}); new MutationObserver(() => { const s = document.querySelector('#scene'); if (s && !window._bodyAt) window._bodyAt = document.body.className; }).observe(document, { childList: true, subtree: true });")
    ph.goto((www / "index.html").as_uri()); ph.wait_for_timeout(900)
    ok("skin-neusky" in (ph.evaluate("_bodyAt") or "") and "light" in ph.evaluate("_bodyAt"), f"телефон впервые — «Небесный» ещё до app.js: {ph.evaluate('_bodyAt')}")
    ok(ph.evaluate("lookSkin()") == "neusky", "телефон: тема по умолчанию — «Небесный»")
    ph.evaluate("() => { S.skin = 'neudusk'; saveS(); applyLook(); }")
    ph.reload(); ph.wait_for_timeout(900)
    ok("skin-neudusk" in (ph.evaluate("_bodyAt") or "") and "light" not in ph.evaluate("_bodyAt"), f"выбранная тема — сразу при следующем открытии: {ph.evaluate('_bodyAt')}")
    ok(not errs, f"без ошибок JavaScript: {errs[:2]}")
    b.close()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
