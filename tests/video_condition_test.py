"""Video 1:05:48: both weather branches through real core/tools and a scripted local model.

Checks orchestration and persisted reminders, not a real model's weather reasoning.
No external search, billing, messages, microphone or user memory.
"""
import asyncio
import json
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from exam import Core

MODE = {"rain": True}
SEARCHES = []
DECISIONS = []


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/search"):
            SEARCHES.append(MODE["rain"])
            weather = "Завтра в Минске дождь" if MODE["rain"] else "Завтра в Минске сухо, без осадков"
            data = {"results": [{"title": "Тестовый прогноз", "url": "https://weather.invalid/fixture", "content": weather}]}
        else:
            data = {"data": [{"id": "fixture"}]}
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        results = [m for m in msgs if m.get("role") == "tool"]
        calls, text = [], ""
        if not results:
            calls = [("web_search", {"query": "Погода в Минске завтра"})]
        elif results[-1].get("name") == "web_search" or "Тестовый прогноз" in str(results[-1].get("content")):
            content = str(results[-1].get("content"))
            assert "Тестовый прогноз" in content, content
            rain = "дождь" in content.lower()
            DECISIONS.append(rain)
            when = (datetime.now() + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            calls = [("set_reminder", {"at": when.isoformat(), "text": "Взять зонт" if rain else "Полить рассаду"})]
        else:
            text = "Напоминание поставлено по тестовому прогнозу."
        delta = {"content": text} if not calls else {"tool_calls": [
            {"index": i, "id": f"fixture-{len(results)}-{i}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
            for i, (name, args) in enumerate(calls)]}
        raw = {"choices": [{"delta": delta, "finish_reason": "tool_calls" if calls else "stop"}]}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(("data: " + json.dumps(raw, ensure_ascii=False) + "\n\ndata: [DONE]\n\n").encode())


async def check():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for rain in (True, False):
            MODE["rain"] = rain
            with tempfile.TemporaryDirectory(prefix="dewiar-video-condition-") as folder:
                profile = {"kind": "openai", "model": "fixture", "base_url": f"http://127.0.0.1:{server.server_port}/v1", "tools_mode": "native"}
                core = Core(profile, folder)
                try:
                    core.start()
                    await core.connect()
                    await core.call("settings_save", patch={"tools": {"search_url": f"http://127.0.0.1:{server.server_port}", "confirm": {"set_reminder": False}}})
                    answer, events = await core.say("Если завтра в Минске будет дождь, напомни утром взять зонт, иначе полить рассаду.", timeout=30)
                    records = await core.call("reminders")
                    expected = "Взять зонт" if rain else "Полить рассаду"
                    assert len(records) == 1 and records[0]["text"] == expected, (records, answer, events[-5:])
                    due = datetime.fromisoformat(records[0]["at"])
                    assert due.date() == (datetime.now() + timedelta(days=1)).date() and due.hour == 8, due
                    assert DECISIONS[-1] == rain and SEARCHES[-1] == rain
                    print("✅ " + ("Rain → umbrella" if rain else "Dry → seedlings") + ": actual search tool, conditional fixture, saved reminder tomorrow 08:00")
                finally:
                    await core.stop()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(check())
