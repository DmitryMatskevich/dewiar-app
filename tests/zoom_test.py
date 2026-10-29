"""Масштаб интерфейса и размер текста (просьба 14.10: «на ноутбуке хочется уменьшить и шрифты, и окна — как настройка
интерфейса в Obsidian»):
— студия «Оформление» → «Размер»: ползунки масштаба (весь интерфейс) и текста (ответы, «Чат», заметки), «Как было»;
  Ctrl − / Ctrl + / Ctrl 0 и Ctrl + колесо в студии; сохраняется в ui.zoom / ui.text;
— виджет: масштаб из настроек — сразу (CSS zoom) и окно под него (Api.set_zoom); Ctrl − в виджете тоже работает;
— ui/app.py: размеры окон виджета под масштаб, правый нижний угол карточки на месте, скругление — тоже под масштаб;
  студия открывается не больше экрана ноутбука."""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
PY = os.environ.get("DEWIAR_PY") or sys.executable
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ── 1. ui/app.py: окна под масштаб ──
sys.path.insert(0, str(ROOT / "ui"))
import app as A  # noqa: E402

ok(A.zoom_of("x") == 1.0 and A.zoom_of(3) == 1.5 and A.zoom_of(0.2) == 0.7 and A.zoom_of(0.9) == 0.9, "ui.zoom: 70–150 %, мусор — 100 %")


class GW:
    def __init__(self): self.calls = []; self.vis = True
    def set_size_request(self, w, h): self.calls.append(("req", w, h))
    def resize(self, w, h): self.calls.append(("resize", w, h))
    def get_visible(self): return self.vis


api = A.Api("t")
gws = {"card": GW(), "mini": GW()}
placed = []
api._gtk = lambda fn, timeout=3: fn()
api._gw = lambda m: gws[m]
api.save_pos = lambda m="card": [1000, 600]            # карточка 300×420 стоит в (1000, 600) — угол (1300, 1020)
api._place = lambda m, cx, cy: placed.append((m, cx, cy)) or [cx, cy]
A.set_zoom_sizes(1)
p = api.set_zoom(0.8)
ok(A.SIZES == {"card": (240, 336)} and ("resize", 240, 336) in gws["card"].calls,
   f"виджет 80 %: окно 240×336 (шарика больше нет): {A.SIZES}")
ok(p == [1060, 684] and placed == [("card", 1060, 684)], f"правый нижний угол карточки на месте (1300, 1020): {p}")
ok(A.card_r() == 22, f"скругление окна — под масштаб: {A.card_r()}")
ok(api.set_zoom(0.8) is None and len(placed) == 1, "тот же масштаб — окна не трогаем")
api.in_tray = True; placed.clear()
api.set_zoom(1.0)
ok(A.SIZES["card"] == (300, 420) and not placed, "виджет в трее — только размер, без показа")
A.set_zoom_sizes(1)

# ── 2. студия и виджет в браузере (ядро — настоящее, модель не нужна) ──
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
PORT = free()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "lira", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False},
    "ui": {"intro": "done", "welcome": "done", "news_seen": "99.0.0"}, "report": {"pulse": False}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
for _ in range(150):
    time.sleep(0.2)
    if (tmp / "cfg/token").exists():
        break
time.sleep(2.5)
tok = (tmp / "cfg/token").read_text().strip()
errs = []


def ui_saved():
    return json.loads((tmp / "cfg/settings.json").read_text()).get("ui", {})


try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        st = b.new_page(viewport={"width": 1366, "height": 768})          # ноутбук
        st.on("pageerror", lambda x: errs.append("студия: " + str(x)))
        st.goto((ROOT / "ui/settings.html").as_uri() + f"?token={tok}&port={PORT}#look"); st.wait_for_timeout(2500)
        hw = b.new_page(viewport={"width": 300, "height": 420})
        hw.on("pageerror", lambda x: errs.append("виджет: " + str(x)))
        hw.add_init_script("window.__zoom = []; window.pywebview = { api: { set_zoom: (z) => { window.__zoom.push(z); return Promise.resolve([5, 6]); },"
                           " save_pos: () => Promise.resolve([0, 0]), get_pos: () => Promise.resolve([0, 0]), set_mode: () => Promise.resolve(null) } };")
        hw.goto((ROOT / "ui/hud.html").as_uri() + f"?token={tok}&port={PORT}&mode=card"); hw.wait_for_timeout(2000)
        S0 = st.evaluate("() => ({ box: !!document.querySelector('#zm'), z: document.getElementById('zm-zoom').value, v: document.getElementById('zm-zoom-v').textContent })")
        ok(S0["box"] and S0["v"] == "100 %", f"«Оформление» → «Размер»: масштаб и текст, сейчас 100 %: {S0}")
        # Ctrl − в студии: сразу мельче, сохраняется, виджет подгоняет окно
        st.click("#p-look h1"); st.keyboard.press("Control+Minus"); st.wait_for_timeout(1500)
        z1 = st.evaluate("() => [document.documentElement.style.zoom, document.getElementById('zm-zoom-v').textContent, document.getElementById('toast').textContent]")
        ok(z1[0] == "0.9" and z1[1] == "90 %" and "90" in z1[2], f"Ctrl − в студии: 90 %, подсказка: {z1}")
        ok(abs(float(ui_saved().get("zoom", 0)) - 0.9) < 1e-6, f"масштаб сохранён: {ui_saved().get('zoom')}")
        hz = hw.evaluate("() => [document.documentElement.style.zoom, window.__zoom]")
        ok(hz[0] == "0.9" and hz[1] == [0.9], f"виджет: страница 90 % и окно под неё (set_zoom): {hz}")
        # колесо с Ctrl — крупнее, один шаг за жест
        st.mouse.move(600, 400); st.keyboard.down("Control"); st.mouse.wheel(0, -120); st.keyboard.up("Control"); st.wait_for_timeout(1200)   # одно событие: под нагрузкой два подряд приходят дальше 120 мс
        ok(st.evaluate("() => document.documentElement.style.zoom") == "", f"Ctrl + колесо вверх — обратно к 100 %: {st.evaluate('() => document.documentElement.style.zoom')}")
        # текст: ползунок — сразу, чат и заметки крупнее, кнопки — нет
        btn0 = st.evaluate("() => getComputedStyle(document.querySelector('#zm-reset')).fontSize")
        st.evaluate("() => { const r = document.getElementById('zm-text'); r.value = 1.2; r.dispatchEvent(new Event('input')); }"); st.wait_for_timeout(900)
        T = st.evaluate("() => [getComputedStyle(document.querySelector('.zm-prev .cl-m .tx')).fontSize, document.getElementById('zm-text-v').textContent, getComputedStyle(document.querySelector('#zm-reset')).fontSize]")
        ok(T[0] == "16.8px" and T[1] == "120 %" and T[2] == btn0, f"текст 120 %: сообщения 14 → 16,8 px, кнопки как были: {T}")
        hw.wait_for_timeout(300)
        ok(hw.evaluate("() => getComputedStyle(document.getElementById('cap')).fontSize") == "16.2px", "виджет: текст ответа тоже крупнее (13,5 → 16,2 px)")
        ok(abs(float(ui_saved().get("text", 0)) - 1.2) < 1e-6, "размер текста сохранён")
        # Ctrl − в самом виджете
        hw.click("body", position={"x": 150, "y": 60}); hw.keyboard.press("Control+Minus"); hw.wait_for_timeout(1500)
        ok(abs(float(ui_saved().get("zoom", 0)) - 0.9) < 1e-6 and hw.evaluate("() => window.__zoom.slice(-1)[0]") == 0.9,
           f"Ctrl − в виджете: 90 %, сохранено: {ui_saved().get('zoom')}")
        st.wait_for_timeout(500)
        ok(st.evaluate("() => document.getElementById('zm-zoom-v').textContent") == "90 %", "студия узнала об изменении из виджета")
        # ноутбук 1366×768 при 80 %: студия без горизонтальной прокрутки
        st.evaluate("() => { const r = document.getElementById('zm-zoom'); r.value = 0.8; r.dispatchEvent(new Event('change')); }"); st.wait_for_timeout(1200)
        W = st.evaluate("() => [document.documentElement.style.zoom, document.documentElement.scrollWidth <= innerWidth + 1]")
        ok(W[0] == "0.8" and W[1], f"80 %: студия без горизонтальной прокрутки: {W}")
        st.click("#zm-reset"); st.wait_for_timeout(1200)
        R = st.evaluate("() => [document.documentElement.style.zoom, getComputedStyle(document.documentElement).getPropertyValue('--dw-text').trim()]")
        ok(R == ["", "1"] and float(ui_saved().get("zoom")) == 1 and float(ui_saved().get("text")) == 1, f"«Как было» — 100 % и обычный текст: {R}")
        st.screenshot(path=os.environ.get("ZOOM_SHOT") or str(tmp / "look.png"))
        b.close()
finally:
    core.terminate()
ok(not errs, "без ошибок JavaScript" + (f": {errs}" if errs else ""))
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
