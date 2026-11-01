"""Страницы через браузер (browserpage.py, 30.10): Лира ответила «Страница закрыта — сайт не пускает без браузера».
Локальный сайт отдаёт обычному запросу (httpx, User-Agent Dewiar) 403 «Just a moment…», а браузеру — страницу-проверку,
которая через секунду сама показывает текст (как Cloudflare). Проверяем:
— read_webpage сам открывает такую страницу в браузере в фоне и возвращает её текст;
— пустая страница с текстом на скриптах — тоже через браузер;
— проверка, которая не проходит сама, — модели честная просьба спросить человека про окно (visible=true), а не выдумка;
— без браузера / с выключенной настройкой / в тестовом режиме браузер не запускается, обычные сайты — как раньше;
— visible=true на сервере без экрана — отказ.
Нужен Chromium-браузер (или путь в DEWIAR_BROWSER); нет — браузерные проверки пропускаются."""
import os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ["no_proxy"] = os.environ["NO_PROXY"]
import browserpage as bp  # noqa: E402
import tools as T  # noqa: E402

fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


BODY = "Текст песни и перевода. " * 40
WALL = ('<html><head><meta charset="utf-8"><title>Just a moment...</title></head><body>'
        '<div id="cf-chl-widget">Checking your browser</div></body></html>')
SOLVE = ('<html><head><meta charset="utf-8"><title>Just a moment...</title></head><body><div>Checking your browser</div>'
         '<script>setTimeout(()=>{document.title="Песня";document.body.innerHTML="<main><h1>Перевод</h1><p>' + BODY +
         '</p></main>"},1200)</script></body></html>')
SHELL = ('<html><head><meta charset="utf-8"><title>Приложение</title></head><body><div id="app"></div>'
         '<script>document.getElementById("app").innerHTML="<article><p>' + BODY + '</p></article>"</script></body></html>')
PLAIN = '<html><head><meta charset="utf-8"></head><body><main><p>' + BODY + '</p></main></body></html>'
HITS = {"browser": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        bot = "Dewiar/" in (self.headers.get("User-Agent") or "")
        if not bot:
            HITS["browser"] += 1
        code, page = 200, PLAIN
        if self.path == "/wall":
            code, page = (403, WALL) if bot else (200, SOLVE)
        elif self.path == "/stuck":
            code, page = 403, WALL
        elif self.path == "/js":
            page = SHELL
        b = page.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
U = f"http://127.0.0.1:{srv.server_port}"
bp.PROFILE = Path(tempfile.mkdtemp(prefix="dewiar-browser-test-"))
bp.HEADLESS_WAIT = 6


class Off:
    def get(self, sec, key=None):
        return False if (sec, key) == ("net", "browser_pages") else None


# без браузера: обычная страница — как раньше, стена — понятный отказ без выдумки
r = T.t_read_webpage({"url": U + "/plain"}, {})
ok("Текст песни" in r and "браузере Dewiar" not in r, "обычный сайт читается без браузера")
try:
    T.t_read_webpage({"url": U + "/wall"}, {})
    ok(False, "без браузера стена — ToolError")
except T.ToolError as e:
    ok("только браузер" in str(e), f"без браузера стена — понятная причина: {e}")
try:
    T.t_read_webpage({"url": U + "/wall"}, {"browser": bp, "settings": Off()})
    ok(False, "настройка выключена — ToolError")
except T.ToolError as e:
    ok("только браузер" in str(e), "настройка «Страницы через браузер» выключена — браузер не запускается")
try:
    T.t_read_webpage({"url": U + "/wall", "visible": True}, {"browser": bp, "server": True})
    ok(False, "visible на сервере — ToolError")
except T.ToolError as e:
    ok("сервере" in str(e), "visible=true на сервере без экрана — отказ")
os.environ["DEWIAR_NO_BROWSER"] = "1"
try:
    bp.fetch(U + "/wall")
    ok(False, "DEWIAR_NO_BROWSER — NoBrowser")
except bp.NoBrowser:
    ok(True, "тестовый режим / стоп-кран — браузер не запускается")
del os.environ["DEWIAR_NO_BROWSER"]
ok(bp.is_challenge(WALL) and not bp.is_challenge(PLAIN) and not bp.is_challenge(SHELL), "распознавание страницы-проверки")

if not bp.find_browser():
    print("⚠️  Chromium-браузер не найден — браузерные проверки пропущены (укажите DEWIAR_BROWSER)")
else:
    ctx = {"browser": bp}
    r = T.t_read_webpage({"url": U + "/wall"}, ctx)
    ok("Текст песни" in r and "Открыто в браузере Dewiar" in r, "стена «Just a moment…» — прочитано через браузер в фоне")
    r = T.t_read_webpage({"url": U + "/js"}, ctx)
    ok("Текст песни" in r and "браузере Dewiar" in r, "текст на скриптах — прочитан через браузер")
    n = HITS["browser"]
    r = T.t_read_webpage({"url": U + "/plain"}, ctx)
    ok(HITS["browser"] == n and "браузере Dewiar" not in r, "обычный сайт — без запуска браузера")
    r = T.t_read_webpage({"url": U + "/stuck"}, ctx)
    ok(r == T.BROWSER_NEED_USER and "visible=true" in r, "проверка не проходит сама — модель спрашивает человека про окно")
    import subprocess, time
    time.sleep(1)
    left = subprocess.run(["pgrep", "-f", str(bp.PROFILE)], capture_output=True, text=True).stdout.split()
    ok(not left, "после чтения браузер закрыт — процессов с профилем Dewiar не осталось")

srv.shutdown()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов: {len(fails)}")
sys.exit(1 if fails else 0)
