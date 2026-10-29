"""Отчёт №133 (20.10, 4 ядра, Ollama на процессоре): «вечно в думках». Каждый вопрос сначала ждал ответа с инструментами
до таймаута (180 с), потом ещё раз — без них. Теперь: своя модель не дождалась ответа с инструментами — 20 минут
спрашиваем её без них (один запрос, а не два), и один раз объясняем почему.
Заглушка модели: с полем tools молчит дольше таймаута профиля (3 с), без него отвечает сразу."""
import asyncio, http.server, json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


SEEN = []


class M(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        b = b'{"data":[{"id":"m"}]}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(bool(body.get("tools")) or "<tools>" in json.dumps(body.get("messages", []), ensure_ascii=False))
        if os.environ.get("SLOW_DEBUG"):
            j = json.dumps(body, ensure_ascii=False); print("REQ", sorted(body.keys()), len(j), "set_reminder" in j, "tool_call" in j)
        if SEEN[-1]:
            time.sleep(5)
        try:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write('data: {"choices":[{"delta":{"content":"Привет! Всё хорошо."}}]}\n\ndata: [DONE]\n\n'.encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


LLM, PORT = free(), free()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", LLM), M)
threading.Thread(target=srv.serve_forever, daemon=True).start()
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
base = f"http://127.0.0.1:{LLM}/v1"
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "lira", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "ui": {"welcome": "done", "intro": "done"}, "report": {"pulse": False},
    "llm": {"active": "o", "profiles": {"o": {"name": "Ollama", "kind": "openai", "base_url": base, "model": "m", "timeout": 3}},
            "tools_modes": {f"{base}|m": "native"}, "tools_probe_v": 2}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(ROOT / "tests/silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
import websockets  # noqa: E402
notes, answers = [], []


async def run():
    ws = None
    for _ in range(300):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError:
                continue
    await ws.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    done = asyncio.Event()

    async def reader():
        async for raw in ws:
            e = json.loads(raw)
            if e.get("type") == "status" and (e.get("status") or {}).get("note"):
                notes.append(e["status"]["note"])
            if e.get("type") == "answer" and e.get("done"):
                answers.append(e.get("text") or ""); done.set()
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(2)
    for i, q in enumerate(("Привет, как дела?", "Расскажи что-нибудь")):
        done.clear(); t0 = time.monotonic()
        await ws.send(json.dumps({"id": i + 1, "cmd": "say", "args": {"text": q}}))
        await asyncio.wait_for(done.wait(), 40)
        answers.append(round(time.monotonic() - t0, 1))
    rd.cancel(); await ws.close()

try:
    asyncio.run(run())
finally:
    core.terminate()
first, second = SEEN[:2], SEEN[2:]
ok(first == [True, False], f"первый вопрос: с инструментами не дождались — без них ответила: {SEEN}")
ok(second and not any(second), f"второй вопрос — сразу без инструментов, без ожидания: {second}")
times = [a for a in answers if isinstance(a, float)]
ok(len(times) == 2 and times[1] < 3, f"второй ответ — быстро: {times}")
ok(any("не успевает отвечать с действиями" in n for n in notes), f"один раз объяснили, почему без действий: {notes[-3:]}")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
