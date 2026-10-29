"""Отчёт 70 (16.10): «сгенерь картинку „Жека-пулемёт“» — модель показала картинки из интернета, человек открыл одну
крупно, и виджет «завис» — пришлось перезапускать программу. Причина: карточка захватывала указатель
(setPointerCapture) при нажатии, и щелчок «закрыть» уходил карточке, а не большой картинке, — она закрывала собой всё.
Проверяем: большая картинка закрывается щелчком мышью (настоящим нажатием), крестиком и Esc; на просьбу нарисовать
картинку инструмент show_images ищет саму тему и говорит модели честно, что это найденное, а не нарисованное."""
import base64, io, os, sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


# ---------- ядро: «сгенерь картинку» ----------
import engine_tools as ET  # noqa: E402

rx = ET.GEN_IMG_RX
ok(all(rx.search(q) for q in ('сгенерь картинку "Жека-пулемет" типо нарисованного танка', "нарисуй кота в шляпе",
                               "сгенерируй мне изображение заката", "сделай логотип для кафе"))
   and not any(rx.search(q) for q in ("покажи генерала Кутузова", "покажи картинку кота", "как выглядит нарисованный танк")),
   "просьба нарисовать — отличается от «покажи картинку»")


class TB:
    def policy(self, n): return True, False
    def title(self, n): return "Картинки"
    def guard(self, n, a): return None, False, None
    def auto_ok(self, n, a): return True
    def run(self, n, a):
        self.args = dict(a)
        return {"text": "На экране показано картинок: 4. Что на них: 1. Как нарисовать пулемёт на танке", "images": []}


class Eng(ET.ToolsMixin):
    def __init__(self):
        self.toolbox = TB(); self.events = []

    def emit(self, *a, **k):
        self.events.append((a, k))


e = Eng()
turn = SimpleNamespace(asked='сгенерь картинку "Жека-пулемет" типо нарисованного танка')
res = e._use_tool(turn, {"name": "show_images", "args": {}}, [])
q = e.toolbox.args.get("query", "")
ok("сгенер" not in q and "картинк" not in q and "Жека-пулемет" in q, f"ищем саму тему, без «сгенерь картинку»: {q!r}")
ok(isinstance(res, dict) and "найденные в интернете" in res["text"] and "не умеет" in res["text"],
   "модели сказано честно: это найденное, рисовать Dewiar не умеет")
res2 = e._use_tool(SimpleNamespace(asked="покажи картинки танка Тигр"), {"name": "show_images", "args": {"query": "танк Тигр"}}, [])
ok("не умеет" not in res2["text"], "обычное «покажи картинки» — без оговорки")

# ---------- виджет: большая картинка закрывается ----------
from playwright.sync_api import sync_playwright  # noqa: E402
from PIL import Image  # noqa: E402
buf = io.BytesIO(); Image.new("RGB", (320, 200), (90, 110, 60)).save(buf, "PNG")
PNG = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
FAKE = ("window.pywebview = { api: new Proxy({}, { get: (_, k) => k === 'get_pos' ? () => Promise.resolve([100, 100]) "
        ": () => Promise.resolve(true) }) };")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 300, "height": 420}); errs = []
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.add_init_script(FAKE)
    pg.goto((ROOT / "ui/hud.html").as_uri() + "?mode=card"); pg.wait_for_timeout(1200)
    show = ("(src) => core.emit('media', { source: 'pics', items: [{ src, title: 'Как нарисовать пулемёт на танке', site: 'megavtogal.com' }] })")
    is_on = "document.getElementById('lightbox').classList.contains('on')"
    res = []
    for how in ("mouse", "x", "esc"):
        pg.evaluate(show, PNG); pg.wait_for_timeout(200)
        pg.evaluate("document.querySelector('#media img').click()"); pg.wait_for_timeout(200)
        opened = pg.evaluate(is_on)
        if how == "mouse":                     # настоящее нажатие мышью посередине картинки (pointerdown → up → click)
            pg.mouse.click(150, 200)
        elif how == "x":
            pg.mouse.click(*(lambda r: (r["x"] + r["width"] / 2, r["y"] + r["height"] / 2))(pg.evaluate(
                "(() => { const r = document.getElementById('lb-x').getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; })()")))
        else:
            pg.keyboard.press("Escape")
        pg.wait_for_timeout(250)
        res.append((how, opened, pg.evaluate(is_on)))
    ok(all(o and not c for _, o, c in res), f"большая картинка открывается и закрывается — щелчком мыши, крестиком и Esc: {res}")
    ok(not errs, f"без ошибок на странице: {errs[:3]}")
    b.close()

print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
