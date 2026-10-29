"""Ядро в режиме сервера (main.py --server): без микрофона и звука, но задачи, ответы текстом и уведомления работают —
так Dewiar на своём сервере круглосуточно берёт задачи с телефона (packaging/dewiar-server.service)."""
import asyncio, http.server, json, os, re, subprocess, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
tmp = Path(tempfile.mkdtemp())
fails = []
def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)

PORT, LLM_PORT = 8829, 8159


class LLM(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"data": [{"id": "chat"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        sysm = body["messages"][0]["content"] if body.get("messages") else ""
        tools = [t["function"]["name"] for t in body.get("tools") or []]
        m = re.search(r"ЗАДАЧА: (.+)", sysm)
        if m and tools:
            n = sum(1 for x in body["messages"] if x["role"] == "tool")
            plan = [("write_file", {"path": "outputs/итог.md", "content": "# Сводка\n\nСделано на сервере."}),
                    ("show", {"path": "outputs/итог.md", "title": "Сводка"}),
                    ("finish", {"summary": "Сводка готова — сделано на сервере."})]
            name, args = plan[min(n, 2)]
            ch = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": f"c{n}", "type": "function",
                                                          "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}]}]
        elif not tools and sysm.startswith("Ты — строгий"):
            ch = [{"choices": [{"delta": {"content": '{"verdict": "ok"}'}}]}]
        else:
            ch = [{"choices": [{"delta": {"content": "Сервер на связи."}}]}]
        if not body.get("stream"):
            text = "".join(c["choices"][0]["delta"].get("content", "") for c in ch)
            b = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for c in ch:
            self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


srv = http.server.ThreadingHTTPServer(("127.0.0.1", LLM_PORT), LLM)
threading.Thread(target=srv.serve_forever, daemon=True).start()
import websockets  # noqa: E402
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False}, "sync": {"enabled": False},
    "tasks": {"self_check": False, "learn": False},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}},
            "tools_modes": {f"http://127.0.0.1:{LLM_PORT}/v1|chat": "native"}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
           PULSE_SERVER="unix:/nonexistent")                       # звука нет вообще — как на сервере
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--server"], env=env, stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events, R = [], {}


async def run():
    ws_ = None
    for _ in range(300):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try:
                ws_ = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError:
                continue
    await ws_.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]; replies = {}
    async def call(cmd, **args):
        n[0] += 1; i = n[0]; await ws_.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
        for _ in range(600):
            if i in replies: return replies.pop(i)
            await asyncio.sleep(0.05)
    async def wait_for(pred, limit):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            for e in events:
                if pred(e): return e
            await asyncio.sleep(0.1)
    async def reader():
        async for raw in ws_:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    end = time.monotonic() + 60
    while time.monotonic() < end and "Режим сервера" not in (tmp / "core.log").read_text(encoding="utf-8"):
        await asyncio.sleep(0.3)
    R["task"] = await call("task_create", title="Сводка", goal="Сделать короткую сводку")
    R["done"] = await wait_for(lambda e: e["type"] == "task" and e["task"].get("status") == "done", 60)
    R["push"] = await wait_for(lambda e: e["type"] == "push" and "Сводка" in (e.get("title", "") + e.get("text", "")), 20)
    R["answer"] = await wait_for(lambda e: e["type"] == "answer" and "готова" in e.get("text", ""), 20)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); core.wait(timeout=20)
    srv.shutdown()
log = (tmp / "core.log").read_text(encoding="utf-8")
ok("Режим сервера: без микрофона и звука" in log, "ядро запустилось в режиме сервера")
ok("Микрофон:" not in log and "Traceback" not in log, "микрофон не открывался, падений нет" + ("" if "Traceback" not in log else ": " + log[log.find("Traceback"):][:600]))
ok(R.get("done") is not None, "задача выполнена на сервере")
ok(R.get("push") is not None, "итог — уведомлением (в Telegram и телефон)")
ok(R.get("answer") is not None, "объявление — текстом, без голоса")
ok(any((tmp / "ws").rglob("итог.md")), "файл результата на месте")
print("ALL OK" if not fails else f"ошибок: {len(fails)}\n" + log[-2500:])
sys.exit(1 if fails else 0)
