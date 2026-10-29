"""Крошечный MCP-сервер для тестов: stdio (по умолчанию) или HTTP (--http PORT [--sse]).
Инструменты: echo (только чтение) и write_note (меняет данные — нужно подтверждение)."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
NOTES = []
TOOLS = [{"name": "echo", "description": "Повторить текст", "annotations": {"readOnlyHint": True},
          "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
         {"name": "write_note", "description": "Записать заметку",
          "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]

def handle(msg):
    m, i = msg.get("method"), msg.get("id")
    if i is None:
        return None
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "1"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": TOOLS}}
    if m == "tools/call":
        a = msg["params"]["arguments"]
        if msg["params"]["name"] == "echo":
            return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": "эхо: " + a["text"]}]}}
        NOTES.append(a["text"])
        return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": f"записано, всего {len(NOTES)}"}]}}
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "no"}}

if "--http" in sys.argv:
    port, sse = int(sys.argv[sys.argv.index("--http") + 1]), "--sse" in sys.argv
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = handle(msg)
            if out is None:
                self.send_response(202); self.end_headers(); return
            b = json.dumps(out, ensure_ascii=False)
            self.send_response(200); self.send_header("Mcp-Session-Id", "s1")
            if sse:
                self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                self.wfile.write(f"event: message\ndata: {b}\n\n".encode())
            else:
                self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b.encode())
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
else:
    for line in sys.stdin:
        out = handle(json.loads(line))
        if out is not None:
            print(json.dumps(out, ensure_ascii=False), flush=True)
