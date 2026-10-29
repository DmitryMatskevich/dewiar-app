"""Отчёт №69 (Windows, 0.38.57): «дал задачу на анализ контента сайта по ссылке — долго отвечала и вернула ничего».
— справочник на Nextra: read_webpage отдавал боковое меню («Skip to Content CTRL K Architecture Checklists…») вместо
  текста — теперь <aside> убираем, берём <main>/<article>, а в конце — список разделов того же сайта (их можно открыть);
— модель читала страницу и искала, а ответ так и не сказала («Не получилось: модель вернула пустой ответ») — теперь
  её просят ответить по найденному без новых действий; и тогда пусто — честная фраза, что дальше;
— соединение с сервером модели не установилось (сеть моргнула) — ещё одна попытка, а не «не ответил вовремя»;
— Windows: значок-круг на мониторе с масштабом 100 % был овалом (окно не уже минимальной ширины) — круг по меньшей стороне."""
import asyncio, json, os, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import websockets
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


SIDE = "".join(f'<li><a href="/docs/{k}">{t}</a></li>' for k, t in [("architecture", "Architecture"), ("checklists", "Checklists"),
                                                                    ("mr", "Самопроверка перед MR"), ("testing", "Testing"), ("tools", "Tooling")])
PAGE = ("<html><head><title>Frontend Engineering Handbook</title></head><body><header>Skip to Content CTRL K</header>"
        f"<aside><ul>{SIDE * 30}</ul></aside><main><h1>Frontend Engineering Handbook</h1><p>"
        + "Стек: React, TypeScript, Vite, Vitest, Playwright, ESLint, Prettier, Storybook. " * 6 + "</p></main><footer>©</footer></body></html>")
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            b = json.dumps({"data": [{"id": "m"}]}).encode(); ct = "application/json"
        else:
            b = PAGE.encode(); ct = "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        SEEN.append(body)
        last = msgs[-1]
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()

        def send(delta, fin=None):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': delta, 'finish_reason': fin}]}, ensure_ascii=False)}\n\n".encode())
        if last["role"] == "user" and "Ответь мне сейчас словами" in str(last["content"]):
            send({"content": "На сайте — React, TypeScript, Vitest и Playwright. Для QA можно собрать такой же справочник."}); send({}, "stop")
        else:                                  # модель снова и снова читает ту же страницу и молчит
            send({"tool_calls": [{"index": 0, "id": f"c{len(SEEN)}", "type": "function", "function": {"name": "read_webpage",
                  "arguments": json.dumps({"url": URL, "find": "инструменты"})}}]}); send({}, "tool_calls")
        self.wfile.write(b"data: [DONE]\n\n")


UP, PORT = free(), free()
URL = f"http://127.0.0.1:{UP}/"
srv = ThreadingHTTPServer(("127.0.0.1", UP), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# read_webpage — сам по себе
import tools as T  # noqa: E402
r = T.t_read_webpage({"url": URL}, {})
ok(r.startswith("Frontend Engineering Handbook") and "Vitest" in r[:400] and "Skip to Content" not in r, f"страница — основной текст, не меню: {r[:90]!r}")
ok("Разделы этого сайта" in r and f"Testing — http://127.0.0.1:{UP}/docs/testing" in r and r.count("/docs/testing") == 1,
   "в конце — разделы сайта со ссылками, без повторов")

tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
base = f"http://127.0.0.1:{UP}/v1"
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "lira", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False}, "ui": {"intro": "done"},
    "report": {"pulse": False}, "team": {"route": False},
    "llm": {"active": "m", "tools_probe_v": 2, "tools_modes": {f"{base}|m": "native"},
            "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": base, "model": "m"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")
OUT = {}


async def session():
    core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                            stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
    try:
        ws_ = None
        for _ in range(300):
            await asyncio.sleep(0.2)
            if (tmp / "cfg/token").exists():
                try:
                    ws_ = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
                except OSError:
                    continue
        await ws_.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
        await ws_.recv()
        await ws_.send(json.dumps({"id": 1, "cmd": "say", "args": {"text": f"Проанализируй контент сайта {URL} и напиши, какие используются инструменты"}}))
        t0 = time.time()
        while time.time() - t0 < 60:
            e = json.loads(await asyncio.wait_for(ws_.recv(), 60))
            if e.get("type") == "answer" and e.get("done"):
                OUT["text"] = e.get("text", "")
                break
        await ws_.close()
    finally:
        core.terminate()
        try:
            core.wait(10)
        except subprocess.TimeoutExpired:
            core.kill()

asyncio.run(session())
log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
ok("React" in OUT.get("text", "") and "пустой ответ" not in OUT.get("text", ""), f"после чтения и молчания — ответ по найденному: {OUT.get('text', '')[:100]!r}")
ok("Модель замолчала после поиска и чтения" in log, "в журнале — почему переспросили")
tool_msgs = [m for b in SEEN for m in b["messages"] if m.get("role") == "tool"]
ok(tool_msgs and "Разделы этого сайта" in str(tool_msgs[0].get("content")), "модель видит разделы сайта")

# телефон: то же чтение страницы
import shutil  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
www = tmp / "www"
shutil.copytree(ROOT / "android/assets/www", www)
for x in ("entities.js", "rich.js", "skins.js", "skins.css", "privacy.js", "closecrypto.js", "qrcode.js", "call.js", "codes.js"):
    if (ROOT / "ui/assets" / x).exists():
        shutil.copy(ROOT / "ui/assets" / x, www / x)
shutil.copytree(ROOT / "ui/assets/live", www / "live", dirs_exist_ok=True)
(www / "personas.js").write_text(subprocess.run([sys.executable, str(ROOT / "android/tools/personas_json.py"), str(ROOT)],
                                                capture_output=True, text=True, check=True).stdout, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); errs = []
    pg.on("pageerror", lambda x: errs.append(str(x)))
    pg.goto((www / "index.html").as_uri()); pg.wait_for_timeout(900)
    ph = pg.evaluate("async (u) => await readPage(u, '')", URL)
    ok(ph.startswith("Frontend Engineering Handbook") and "Skip to Content" not in ph and "Разделы этого сайта" in ph and ph.count("/docs/testing") == 1,
       f"телефон: страница — основной текст и разделы сайта: {ph[:80]!r}")
    ok(not errs, f"телефон без ошибок: {errs[:2]}")
    b.close()

# соединение не установилось — ещё раз
import httpx, llm  # noqa: E402
n = [0]
orig = httpx.stream


class CM:
    def __enter__(self):
        n[0] += 1
        if n[0] == 1:
            raise httpx.ConnectTimeout("timed out")
        return "ok"

    def __exit__(self, *e): return False


httpx.stream = lambda *a, **k: CM()
try:
    with llm._stream_retry("POST", "http://x") as r_:
        got = r_
finally:
    httpx.stream = orig
ok(got == "ok" and n[0] == 2, "соединение не установилось — вторая попытка")
src = (ROOT / "ui/winshell.py").read_text(encoding="utf-8")
ok("d = min(w, hgt)" in src, "Windows: значок — круг по меньшей стороне окна")
srv.shutdown()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
