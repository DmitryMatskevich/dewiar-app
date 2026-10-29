"""Юнит-тесты этапа 4: Claude tool_use в потоке, разбор DuckDuckGo, поиск программ, подтверждения."""
import json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
from llm import stream_events, tool_turn_messages
from tools import parse_ddg, desktop_apps, find_app, html_to_text

# 1) Anthropic: текст + tool_use с аргументами кусками
class A(BaseHTTPRequestHandler):
    seen = {}
    def log_message(self, *a): pass
    def do_POST(self):
        A.seen = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        evs = [{"type": "message_start"}, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
               {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Открываю. "}},
               {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "open_url", "input": {}}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"url": "https://ya'}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '.ru"}'}},
               {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"}]
        for e in evs:
            self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode())
srv = ThreadingHTTPServer(("127.0.0.1", 8141), A); threading.Thread(target=srv.serve_forever, daemon=True).start()
prof = {"kind": "anthropic", "base_url": "http://127.0.0.1:8141", "api_key": "k", "model": "claude-x"}
spec = [{"name": "open_url", "description": "d", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}]
ev = list(stream_events(prof, "sys", [{"role": "user", "content": "открой яндекс"}], tools=spec))
assert ev[0] == ("text", "Открываю. "), ev
assert ev[-1] == ("tools", [{"id": "toolu_1", "name": "open_url", "args": {"url": "https://ya.ru"}}]), ev
assert A.seen["tools"][0]["input_schema"]["properties"]["url"]
m = tool_turn_messages(prof, "Открываю. ", ev[-1][1], ["ok"])
assert m[0]["content"][1]["type"] == "tool_use" and m[1]["content"][0]["tool_use_id"] == "toolu_1"
print("✅ Claude: текст и вызов инструмента в потоке, формат результата")

# 2) DuckDuckGo HTML
page = '''<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpogoda.ru%2Fmoscow&amp;rut=1">Погода <b>в Москве</b></a>
<a class="result__snippet" href="x">Завтра &laquo;облачно&raquo;, +12</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Реклама</a></div>'''
r = parse_ddg(page)
assert r == [{"title": "Погода в Москве", "url": "https://pogoda.ru/moscow", "snippet": "Завтра «облачно», +12"}], r
assert "Текст" in html_to_text("<html><script>x()</script><p>Текст</p></html>")
print("✅ DuckDuckGo: заголовки, ссылки, реклама отброшена")

# 3) Программы из меню, в том числе по-русски
d = Path(tempfile.mkdtemp())
(d / "org.gnome.Calculator.desktop").write_text("[Desktop Entry]\nType=Application\nName=Calculator\nName[ru]=Калькулятор\nKeywords=calc;\nExec=gnome-calculator\n")
(d / "firefox_firefox.desktop").write_text("[Desktop Entry]\nType=Application\nName=Firefox Web Browser\nName[ru]=Веб-браузер Firefox\nExec=firefox\n")
(d / "org.telegram.desktop.desktop").write_text("[Desktop Entry]\nType=Application\nName=Telegram Desktop\nExec=telegram\n")
(d / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\n")
os.environ["DEWIAR_APPS_DIR"] = str(d)
apps = desktop_apps()
for q, exp in [("калькулятор", "Calculator"), ("firefox", "Firefox Web Browser"), ("браузер", "Firefox Web Browser"),
               ("телеграм", "Telegram Desktop"), ("фаерфокс", "Firefox Web Browser"), ("telegram", "Telegram Desktop"), ("hidden", None)]:
    a = find_app(q, apps); got = a["name"] if a else None
    print(("✅" if got == exp else "❌"), f"«{q}» → {got}")
