"""Компьютер: отказ сервера модели строкой «error» внутри потока (HTTP 200) — «upstream returned HTTP 400», как у
Ollama с облачными моделями. Ядро повторяет запрос сначала без chat_template_kwargs (22.10), затем — описав инструменты
текстом, и вызов <tool_call> распознаётся."""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
import llm  # noqa: E402
seen = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); seen.append(b)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        if b.get("tools"):
            self.wfile.write(b'data: {"error":{"message":"upstream returned HTTP 400"}}\n\n'); return
        t = '<tool_call>\n{"name": "get_time", "arguments": {}}\n</tool_call>'
        self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\ndata: [DONE]\n\n").encode())
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
p = {"kind": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m"}
tools = [{"name": "get_time", "description": "Который час", "parameters": {"type": "object", "properties": {}}}]
out = list(llm.stream_events(p, "Ты ассистент.", [{"role": "user", "content": "Который час?"}], tools=tools))
calls = [v for k, v in out if k == "tools"]
text = "".join(v for k, v in out if k == "text")
ok = len(seen) == 3 and "chat_template_kwargs" not in seen[1] and seen[1].get("tools") and not seen[2].get("tools") and calls and calls[0][0]["name"] == "get_time" and "<tool_call>" not in text
print(("✅ " if ok else "❌ ") + f"отказ внутри потока — повтор с инструментами текстом, вызов распознан: {calls} {text!r}")
sys.exit(0 if ok else 1)
