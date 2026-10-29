"""Виджет (отзыв 19.10, Ubuntu): «грубовато края стали, скругления» и «возможность ресайзить окошко, растягивать ввысь
или вширь с запоминанием».
- Linux с композитором: окно прозрачное (&tw=1), углы скругляет страница — сглаженно; форма окна (X Shape) — с
  меньшим радиусом, только для кликов, и не срезает сглаженный край.
- Карточку тянут за правый нижний угол: окно меняет размер (Api.resize_card), отпустили — размер в настройках
  (ui.hud_w, ui.hud_h при масштабе 100 %); пределы 280×360 … 640×1000; двойной щелчок — как было; при запуске — свой размер.
- Растянутая карточка: панель кнопок внизу видна целиком, портрет не уезжает."""
import json, os, shutil, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg")
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


sys.path.insert(0, str(ROOT / "ui"))
import app  # noqa: E402

default_size = app.set_card_base(None, None)
bounded_size = app.set_card_base(2000, 10)
custom_size = app.set_card_base(360, 560)
ok(default_size == app.CARD_DEFAULT and bounded_size == (app.CARD_MAX[0], app.CARD_MIN[1]) and custom_size == (360, 560),
   "свой размер карточки — в пределах, пусто — по умолчанию")
app.set_zoom_sizes(0.8)
expected_zoom = tuple(round(v * 0.8) for v in custom_size)
ok(app.SIZES["card"] == expected_zoom, f"свой размер и масштаб вместе: {app.SIZES['card']} (ожидалось {expected_zoom})")
app.set_zoom_sizes(1.0)
app.TRANSPARENT[0] = False
solid = app._shape_rects("card", 300, 420, 28)
app.TRANSPARENT[0] = True
smooth = app._shape_rects("card", 300, 420, 28)
app.TRANSPARENT[0] = False
ok(smooth[0][0] < solid[0][0] and len(smooth) < len(solid), "прозрачное окно: форма скругляет меньше — сглаженный край страницы не срезан")
src = (ROOT / "ui/app.py").read_text(encoding="utf-8")
ok('transparent=TRANSPARENT[0]' in src and '"&tw=1" if TRANSPARENT[0]' in src and src.count('set_card_base(uis.get("hud_w")') == 2,
   "виджет создаётся прозрачным при композиторе; свой размер — при запуске на Linux и Windows")

# страница: тянем за угол
ui = tmp / "ui"; shutil.copytree(ROOT / "ui", ui)
(ui / "assets/dewiar.js").write_text("""
window.api = () => window._api;
window.core = { _on: {}, saved: [], on(k, f) { (this._on[k] = this._on[k] || []).push(f); return this; },
  async call(c, a) { if (c === "settings_save") this.saved.push(a.patch); return {}; } };
window._calls = [];
window._api = { card_size: async () => [300, 420], resize_card: async (w, h, fin) => { _calls.push([w, h, fin]); return fin ? (w ? [w, h] : [300, 420]) : null; },
  get_pos: async () => [0, 0], save_pos: async () => [0, 0] };
""", encoding="utf-8")
from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    pg = b.new_page(viewport={"width": 300, "height": 420})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto((ui / "hud.html").as_uri() + "?mode=card&port=1&tw=1"); pg.wait_for_timeout(700)
    T = pg.evaluate("""() => ({ tw: document.documentElement.classList.contains('tw'), bg: getComputedStyle(document.body).backgroundColor,
        r: getComputedStyle(document.querySelector('#card')).borderTopLeftRadius })""")
    ok(T["tw"] and T["bg"] in ("rgba(0, 0, 0, 0)", "transparent") and T["r"] == "28px", f"прозрачное окно: фон страницы прозрачный, углы скругляет карточка — во всё окно, без пустого края: {T}")
    g = pg.locator("#grip").bounding_box()
    ok(g is not None and g["x"] > 260 and g["y"] > 380, f"уголок для растягивания — справа внизу: {g}")
    pg.mouse.move(g["x"] + 9, g["y"] + 9); pg.mouse.down(); pg.wait_for_timeout(100)
    pg.mouse.move(g["x"] + 60, g["y"] + 130, steps=6); pg.wait_for_timeout(200); pg.mouse.up(); pg.wait_for_timeout(300)
    C = pg.evaluate("({ calls: _calls, saved: core.saved, moved: document.querySelector('#card').classList.contains('dragging') })")
    last = C["calls"][-1] if C["calls"] else None
    ok(last and last[2] is True and abs(last[0] - 351) <= 3 and abs(last[1] - 541) <= 3 and len(C["calls"]) >= 2,
       f"тянем угол — окно растёт на ходу, отпустили — окончательный размер: {C['calls'][-3:]}")
    ok({"ui": {"hud_w": last[0], "hud_h": last[1]}} in C["saved"] and not C["moved"], f"размер сохранён в настройки, карточка не поехала: {C['saved']}")
    pg.dblclick("#grip"); pg.wait_for_timeout(300)
    C2 = pg.evaluate("({ calls: _calls, saved: core.saved })")
    ok(C2["calls"][-1] == [0, 0, True] and {"ui": {"hud_w": None, "hud_h": None}} in C2["saved"], "двойной щелчок — размер по умолчанию")
    # растянутая карточка: панель видна целиком
    for w, h in ((420, 640), (280, 360), (640, 1000)):
        pg.set_viewport_size({"width": w, "height": h}); pg.wait_for_timeout(300)
        R = pg.evaluate("() => { const b = document.querySelector('#bar').getBoundingClientRect(), o = document.querySelector('#orb, #cv').getBoundingClientRect(); return { bb: b.bottom, br: b.right, bl: b.left, ih: innerHeight, iw: innerWidth, ot: o.top }; }")
        ok(R["bb"] <= R["ih"] and R["bl"] >= 0 and R["br"] <= R["iw"] and R["ot"] >= 0, f"{w}×{h}: панель кнопок и портрет на месте: {R}")
    ok(not errs, f"без ошибок: {errs[:2]}")
    b.close()
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
