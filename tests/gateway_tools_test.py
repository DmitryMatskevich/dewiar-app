"""Шлюз, который молча выбрасывает поле tools (модель отвечает «не могу»), и шлюз, который на tools отвечает пустотой:
Dewiar сам определяет, как с ними работать, и действия выполняются."""
import http.server, json, os, re, socket, sys, tempfile, threading
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg")
sys.path.insert(0, str(ROOT / "core"))
import llm  # noqa: E402
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
SEEN = []


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        self._json({"data": [{"id": "chat"}]} if self.path.endswith("/models") else {"models": [{"name": "chat"}]})

    def _json(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"] if isinstance(body["messages"][-1]["content"], str) else ""
        mode = self.server.mode
        if body.get("tools") and mode == "empty":
            text = ""                                               # шлюз «съедает» ответ с инструментами
        elif "<tools>" in system and body["messages"][-1]["role"] == "user" and "tool_response" not in user:
            name = "get_time" if "час" in user else "play_music" if "музык" in user else None
            text = f'<tool_call>\n{{"name": "{name}", "arguments": {{}}}}\n</tool_call>' if name else "Хорошо."
        elif "tool_response" in user:
            text = "Готово, включила."
        else:
            text = "К сожалению, я не могу этого сделать."          # tools полем не дошли до модели
        chunks = [{"choices": [{"delta": {"content": text}}]}] if text else [{"choices": [{"delta": {}, "finish_reason": "stop"}]}]
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def serve(mode):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H); srv.mode = mode
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return {"name": "gw", "kind": "openai", "base_url": f"http://127.0.0.1:{port}/v1", "model": "chat"}


tools = [{"name": "play_music", "description": "включить музыку", "parameters": {"type": "object", "properties": {}}}]

p = serve("drop")
ok(llm.probe_tools(p) == "prompt", "шлюз выбрасывает tools → проверка выбрала «описанием в тексте»")
ev = list(llm.stream_events(dict(p, tools_mode="prompt"), "Ты помощник.", [{"role": "user", "content": "включи музыку"}], tools=tools))
ok(any(k == "tools" and v[0]["name"] == "play_music" for k, v in ev), "«включи музыку» → вызов play_music")
r = llm.test_profile(p)
ok(r["ok"] and r.get("tools_mode") == "prompt", f"кнопка «Проверить» сообщает режим: {r.get('tools_mode')}")

p2 = serve("empty")
SEEN.clear()
ev = list(llm.stream_events(p2, "Ты помощник.", [{"role": "user", "content": "включи музыку"}], tools=tools))
ok(any(k == "tools" and v[0]["name"] == "play_music" for k, v in ev) and len(SEEN) == 2,
   "пустой ответ на tools → сам повторил с описанием в тексте, действие выполнено")
