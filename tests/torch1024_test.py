"""№227 (24.10, телефон): «Лира включила фонарик, а „выключить“ не сделала и сама удивилась… и произносит команду вслух».
«Лера выключи фонарик» (ослышка имени), «выключи фонарик на телефоне», «фонарик включён, выключи его» уходили модели,
а та отвечала «Фонарик выключен.», не вызвав flashlight. Теперь такие фразы — сразу, без модели; а ответ модели
«фонарик выключен» без flashlight — ПРОВЕРКА (сделай, а не говори)."""
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
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
        pg.evaluate("() => { window._t = []; D.torch = (on) => { window._t.push(on); return true; }; }")
        for q, want in (("Лера Выключи фонарик", False), ("Выключи фонарик на телефоне", False), ("Лира фонарик включён Выключи его", False),
                        ("Лира, включи фонарик", True), ("фонарик выключи", False), ("посвети мне", True), ("погаси фонарь, пожалуйста", False)):
            r = pg.evaluate("async (q) => { window._t = []; const r = await quick(q); return { say: r && r.say, t: window._t }; }", q)
            ok(r["t"] == [want] and r["say"] == ("Фонарик включён." if want else "Фонарик выключен."), f"«{q}» — сразу, без модели: {r}")
        for q in ("не выключай фонарик", "выключи его", "расскажи про фонарик", "что такое flashlight"):
            r = pg.evaluate("async (q) => { window._t = []; await quick(q); return window._t; }", q)
            ok(r == [], f"«{q}» — фонарик не трогаем: {r}")
        c = pg.evaluate("() => { const c = claimedAction('Фонарик выключен.', new Set(), 'Лера выключи фонарик'); return c && c.tool; }")
        ok(c == "flashlight", f"«Фонарик выключен.» без flashlight — ПРОВЕРКА: {c}")
        c = pg.evaluate("() => claimedAction('Фонарик выключен.', new Set(['flashlight']), 'выключи фонарик')")
        ok(c is None, "с вызовом flashlight — всё честно")
        ok(not errs, f"без ошибок: {errs[:2]}")
        b.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    raise SystemExit(1)
