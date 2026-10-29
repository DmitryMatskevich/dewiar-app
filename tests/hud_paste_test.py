"""Картинка из буфера обмена в виджете: Ctrl+V (и когда поле вопроса закрыто) или кнопка «из буфера» — картинку
берёт окно (Api.clipboard_image), она уходит модели вместе с вопросом. Живое ядро, модель-заглушка, страница виджета."""

import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
TESTS = Path(__file__).resolve().parent
PY = os.environ.get("DEWIAR_PY") or sys.executable
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


import base64, io  # noqa: E402
from PIL import Image  # noqa: E402
buf = io.BytesIO(); Image.new("RGB", (640, 480), (200, 60, 90)).save(buf, "PNG")
PNG = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
src = (ROOT / "ui/app.py").read_text(encoding="utf-8")
ok("def clipboard_image(self)" in src and "wait_for_image" in src and "ImageGrab" in src, "окно умеет взять картинку из буфера (Linux и Windows)")

tmp = Path(tempfile.mkdtemp())
LLM, PORT = free(), free()
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM)])
cfg = tmp / "cfg"; cfg.mkdir()
(cfg / "settings.json").write_text(json.dumps({"persona": "dewiar", "audio": {"sounds": False}, "ui": {"welcome": "done"},
                                               "stt": {"engine": "vosk"}, "tts": {"engine": "off"},
                                               "llm": {"active": "m", "profiles": {"m": {"name": "Мок", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "mock"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(cfg), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"))
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
for _ in range(150):
    time.sleep(0.2)
    if (cfg / "token").exists():
        break
time.sleep(2)
tok = (cfg / "token").read_text().strip()
FAKE = "window.CLIP = '%s'; window.pywebview = { api: new Proxy({}, { get: (_, k) => k === 'clipboard_image' ? () => Promise.resolve(window.CLIP) : () => Promise.resolve(true) }) };" % PNG
try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 300, "height": 520}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script(FAKE)
        pg.goto((ROOT / "ui/hud.html").as_uri() + f"?token={tok}&port={PORT}&mode=card"); pg.wait_for_timeout(2500)
        pg.keyboard.press("Control+v"); pg.wait_for_timeout(500)
        n = pg.evaluate("atts.length")
        ok(n == 1 and pg.evaluate("document.getElementById('ask').style.display") == "block", f"Ctrl+V в виджете — поле вопроса с картинкой из буфера: {n}")
        pg.fill("#askin", "Что здесь?")
        pg.click("#a-send")
        cap = ""
        for _ in range(40):
            pg.wait_for_timeout(250)
            cap = pg.evaluate("document.getElementById('cap').textContent")
            if "Вижу картинку" in cap:
                break
        ok("Вижу картинку 640x480" in cap, f"картинка дошла до модели: {cap[:80]}")
        pg.evaluate("window.CLIP = ''")
        # Поле текста теперь открыто постоянно; старую кнопку «Текст» намеренно убрали из панели.
        pg.click("#a-clip"); pg.wait_for_timeout(400)
        ok("нет картинки" in pg.evaluate("document.getElementById('cap').textContent") and pg.evaluate("atts.length") == 0,
           "в буфере нет картинки — понятная подсказка, без отчёта об ошибке")
        ok(not errs, f"без ошибок на странице: {errs}")
        b.close()
finally:
    core.terminate(); mock.terminate(); core.wait(10)
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
