"""vLLM без --enable-auto-tool-choice: функции описываются текстом, вызов <tool_call> вынимается из ответа."""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import llm
SEEN = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"data": [{"id": "chat"}]}).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); SEEN.append(body)
        if body.get("tools"):
            b = b'{"error":{"message":"\\"auto\\" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set"}}'
            self.send_response(400); self.end_headers(); self.wfile.write(b); return
        last = body["messages"][-1]["content"]
        if "<tool_response>" in last:
            text = "Готово, открыл ютуб."
        else:
            assert "<tools>" in body["messages"][0]["content"]
            text = 'Сейчас открою. <tool_call>\n{"name": "open_url", "arguments": {"url": "https://youtube.com"}}\n</tool_call>'
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for i in range(0, len(text), 7):                      # тег приходит кусками по 7 символов
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': text[i:i + 7]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
srv = ThreadingHTTPServer(("127.0.0.1", 8162), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
P = {"kind": "openai", "base_url": "http://127.0.0.1:8162/v1", "model": "chat"}
tools = [{"name": "open_url", "description": "Открыть сайт", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}]
msgs = [{"role": "user", "content": "открой ютуб"}]
ev = list(llm.stream_events(P, "Ты помощник.", msgs, tools=tools))
text = "".join(v for k, v in ev if k == "text"); calls = [v for k, v in ev if k == "tools"]
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("✅" if cond else "❌"), name)
check("сервер отверг функции полем → повтор с описанием в тексте", len(SEEN) == 2 and "tools" not in SEEN[1] and "<tools>" in SEEN[1]["messages"][0]["content"])
check("тег вызова не попал в озвучку", text.strip() == "Сейчас открою." and "tool_call" not in text)
check("вызов вынут из текста", calls and calls[0][0]["name"] == "open_url" and calls[0][0]["args"] == {"url": "https://youtube.com"})
msgs2 = msgs + llm.tool_turn_messages(P, text, calls[0], ["Открыл https://youtube.com"])
check("результат вернули в формате <tool_response>", msgs2[-1]["role"] == "user" and "<tool_response>" in msgs2[-1]["content"])
ev2 = list(llm.stream_events(P, "Ты помощник.", msgs2, tools=tools))
check("модель ответила по результату, без лишних запросов", "".join(v for k, v in ev2 if k == "text") == "Готово, открыл ютуб." and len(SEEN) == 3)
print("ALL OK" if ok else "FAIL"); sys.exit(0 if ok else 1)
