"""Windows-сторож виджета (ui/app.py, page_guard) проверяет, что страница виджета загрузилась, по элементам hud.html.
Ловит случай, когда сторож ищет элемент, которого в виджете нет (#state), — и на каждом запуске Windows прячет рабочий
виджет, показывает «Окно виджета не загрузилось» и открывает студию в браузере. Сторож проверяется на настоящей странице."""
import re, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "ui/app.py").read_text(encoding="utf-8")
js = re.search(r'api\._hud\.evaluate_js\("(.+?)"\)', src).group(1)
fails = []
def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c: fails.append(m)
ids = re.findall(r"getElementById\('([\w-]+)'\)", js)
html = (ROOT / "ui/hud.html").read_text(encoding="utf-8")
ok(ids and all(f'id="{i}"' in html for i in ids), f"элементы проверки есть в виджете: {ids}")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page()
    pg.goto((ROOT / "ui/hud.html").as_uri() + "?token=x&port=1&mode=card"); pg.wait_for_timeout(800)
    r = pg.evaluate(js)
    ok(r.startswith("ok "), f"сторож видит загруженный виджет: {r[:60]}")
    pg.set_content("<html><body>Не удалось открыть страницу</body></html>")
    ok(pg.evaluate(js).startswith("bad "), "страница ошибки — «bad»")
    b.close()
sys.exit(1 if fails else 0)
