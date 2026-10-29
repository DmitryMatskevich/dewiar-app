"""21.10, Ubuntu: «когда что-то удаляю (заметку) — окно выглядит пугающе»: системное окно WebKit «JavaScript —
file:///…/settings.html?token=…» — некрасиво и показывает адрес страницы с ключом связи. Теперь в студии и блокноте —
свои окна вопроса (ui/assets/dialog.js): кнопка называется действием («Удалить»), опасное — красным, Enter — да,
Esc и щелчок мимо — нет; поле ввода — своё же (askText)."""
import os, re, sys
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


bad = []
for p in list((ROOT / "ui/studio").glob("*.js")) + [ROOT / "ui/pad.js"]:
    for i, l in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
        code = l.split("//")[0]
        if re.search(r"(?<![.\w])(confirm|prompt|alert)\(", code):
            bad.append(f"{p.name}:{i}")
ok(not bad, f"в студии и блокноте нет системных confirm/prompt/alert: {bad[:5]}")
html = (ROOT / "ui/settings.html").read_text(encoding="utf-8")
ok('<script src="assets/dialog.js"></script>' in html and '<script src="assets/dialog.js"></script>' in (ROOT / "ui/pad.html").read_text(encoding="utf-8"),
   "окна вопроса подключены в студии и блокноте")

from playwright.sync_api import sync_playwright  # noqa: E402
FAKE = "window.pywebview = { api: new Proxy({}, { get: () => () => Promise.resolve(true) }) };"
with sync_playwright() as p:
    b = p.chromium.launch(); errs, native = [], []
    pg = b.new_page(viewport={"width": 1280, "height": 860}); pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    pg.add_init_script(FAKE)
    pg.goto((ROOT / "ui/settings.html").as_uri() + "?port=1&skin=neusky"); pg.wait_for_timeout(900)
    pg.evaluate("void (window.r1 = ask('Удалить заметку «Идея»? Она уйдёт в корзину — оттуда её можно вернуть.'))")
    pg.wait_for_timeout(300)
    d = pg.evaluate("() => ({ n: document.querySelectorAll('.dwq').length, yes: document.querySelector('.dwq .yes').textContent, "
                    "danger: document.querySelector('.dwq .yes').classList.contains('danger'), no: document.querySelector('.dwq button').textContent, "
                    "focus: document.activeElement === document.querySelector('.dwq .yes'), text: document.querySelector('.dwq p').textContent })")
    ok(d["n"] == 1 and d["yes"] == "Удалить" and d["danger"] and d["no"] == "Отмена" and d["focus"] and "token" not in d["text"],
       f"«Удалить заметку…?» — кнопки «Отмена» и «Удалить» (красная), без адреса страницы: {d}")
    pg.screenshot(path="/tmp/claude-0/dialog-light.png")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(250)
    ok(pg.evaluate("r1") is False and pg.locator(".dwq").count() == 0, "Esc — нет, окно ушло")
    pg.evaluate("void (window.r2 = ask('Выйти из аккаунта dewiar.pro?'))"); pg.wait_for_timeout(250)
    ok(pg.inner_text(".dwq .yes") == "Выйти" and "danger" not in pg.get_attribute(".dwq .yes", "class"), "«Выйти» — не красная")
    pg.keyboard.press("Enter"); pg.wait_for_timeout(250)
    ok(pg.evaluate("r2") is True, "Enter — да")
    pg.evaluate("void (window.r3 = askText('Название новой заметки:', ''))"); pg.wait_for_timeout(250)
    pg.keyboard.type("План на неделю"); pg.keyboard.press("Enter"); pg.wait_for_timeout(250)
    ok(pg.evaluate("r3") == "План на неделю", "своё поле ввода вместо prompt()")
    pg.evaluate("void (window.r4 = ask('Удалить?'))"); pg.wait_for_timeout(250)
    pg.mouse.click(10, 10); pg.wait_for_timeout(250)
    ok(pg.evaluate("r4") is False, "щелчок мимо — нет")
    pg.evaluate("void (window.r5 = messagePopup('Доступ запрещён. Включите Dewiar в системных настройках Контактов.', {title:'Нужен доступ к Контактам macOS', steps:['Откройте настройки macOS.', 'Включите Dewiar в разделе «Контакты».', 'Вернитесь и проверьте доступ.'] }))")
    pg.wait_for_timeout(250)
    info = pg.evaluate("() => ({title: document.querySelector('.dwm h2')?.textContent, text: document.querySelector('.dwm')?.textContent, steps: document.querySelectorAll('.dwm-steps li').length, copy: document.querySelector('.dwm-copy-status')?.textContent || '', close: document.querySelector('.dwm-actions .primary')?.textContent})")
    ok(info['title'] == 'Нужен доступ к Контактам macOS' and info['steps'] == 3 and 'Включите Dewiar' in info['text'] and info['close'] == 'Понятно', f"информативное окно сообщения со всеми шагами и копированием: {info}")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(250)
    ok(pg.evaluate("r5") is True and pg.locator('.dwm').count() == 0, "Esc закрывает информативное окно")
    ok(not native, f"системных окон не было: {native}")
    ok(not errs, f"без ошибок JavaScript: {errs[:2]}")
    b.close()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
