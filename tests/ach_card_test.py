"""Отзыв 16.10 (телефон): карточка «Полсотни · Новое достижение» висела на главном экране, убрать нельзя, и непонятно, что это
за награды. Теперь у карточки ✕ (убирает), нажатие открывает «Мой день» → «Дела» со всеми значками и объяснением «Зачем
это» (первый раз — сразу, потом — кнопкой «?»); пока объяснение не видели, на карточке — «Что это за награды? Нажмите»."""
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
for x in ("entities.js", "rich.js", "skins.js", "skins.css", "privacy.js", "closecrypto.js", "qrcode.js", "call.js", "codes.js"):
    if (ROOT / "ui/assets" / x).exists():
        shutil.copy(ROOT / "ui/assets" / x, www / x)
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
errs = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 400, "height": 820})
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(1000)
    pg.evaluate("() => { const l = []; for (let i = 0; i < 49; i++) l.push({ id: 'x' + i, title: 'Дело ' + i, at: Date.now() - (i + 1) * 864e5 / 3 }); saveDone(l); markDone(['Позвонить маме'], 'tap'); }")
    pg.wait_for_timeout(600)
    c = pg.evaluate("() => { const c = document.querySelector('#achc'); return c && [c.querySelector('b').textContent, c.querySelector('em').textContent, !!c.querySelector('.achx')]; }")
    ok(c == ["Полсотни", "Что это за награды? Нажмите", True], f"карточка достижения: название, подсказка и ✕: {c}")
    pg.click("#achc"); pg.wait_for_timeout(600)
    r = pg.evaluate("""() => [document.querySelector('#sh-day').classList.contains('on'), dayTab, !!document.querySelector('#done-whybox'),
        (document.querySelector('#done-whybox') || {}).textContent || '', document.querySelectorAll('#day-body .ach').length, !!document.querySelector('#achc'), S.doneWhySeen]""")
    ok(r[0] and r[1] == "rem" and r[2] and "значки" in r[3] and r[4] >= 3 and not r[5] and r[6],
       f"нажатие — «Мой день» → «Дела» с объяснением и всеми значками, карточка убрана: {r[:3] + [r[4], r[5], r[6]]}")
    pg.click("#done-whyok"); pg.wait_for_timeout(200)
    ok(not pg.evaluate("!!document.querySelector('#done-whybox')"), "«Понятно» — объяснение скрыто")
    pg.click("#done-why"); pg.wait_for_timeout(200)
    ok(pg.evaluate("!!document.querySelector('#done-whybox')"), "кнопка «?» — объяснение снова")
    pg.evaluate("() => { doneWhy = false; closeSheets(); markDone(['Сделать зарядку', 'Купить хлеб'], 'tap'); }"); pg.wait_for_timeout(400)
    ok(pg.evaluate("document.querySelector('#achc em').textContent") == "Все значки — в «Мой день»", "объяснение уже видели — на карточке короче")
    pg.click("#achc .achx"); pg.wait_for_timeout(200)
    ok(pg.evaluate("[!!document.querySelector('#achc'), document.querySelector('#sh-day').classList.contains('on')]") == [False, False],
       "✕ — карточка убрана, «Мой день» не открывается")
    b.close()
ok(not errs, "без ошибок JavaScript" + (f": {errs}" if errs else ""))
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
