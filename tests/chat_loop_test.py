"""Разговор с действиями на настоящем ядре (сервер) и модели-заглушке, которая упорно повторяет один и тот же поиск:
одинаковый вызов в одном ходе выполняется один раз (дальше — «уже было»), последний шаг — без действий, и ответ —
словами по сути, а не «Готово.». Плюс: команда задачи по коду с путём исходного проекта уходит в рабочую копию."""
import asyncio, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
import exam as X  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


SEEN = []
BLANK = {"n": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = b'{"data": [{"id": "m"}]}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        names = [t["function"]["name"] for t in body.get("tools") or []]
        sysm = body["messages"][0].get("content") if body["messages"][0].get("role") == "system" else ""
        SEEN.append({"tools": names, "final": "Действий больше не будет" in str(sysm)})
        calls, out = [], ""
        last = json.dumps(body["messages"][-3:], ensure_ascii=False)
        if "Сколько стоит чай" in last and BLANK["n"] < 2:          # два пустых ответа подряд — сбой модели
            BLANK["n"] += 1
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return
        if "Сколько стоит чай" in last:
            out = "Чай стоит 100 рублей." if "web_search" in names else "БЕЗ ИНСТРУМЕНТОВ"
        elif "get_time" in names:                                   # проверка, умеет ли модель действия
            calls = [("get_time", {})]
        elif "web_search" in names:                               # упорно ищет одно и то же
            calls = [("web_search", {"query": "тёплые песни"})]
        else:
            out = "Вот пять песен: 1. The Lumineers — Ho Hey; 2. Jack Johnson — Better Together."
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        if out:
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': out}}]}, ensure_ascii=False)}\n\n".encode())
        for k, (name, args) in enumerate(calls):
            tc = {"index": k, "id": f"c{len(SEEN)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [tc]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
prof = {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "m"}


async def main():
    core = X.Core(prof, Path(tempfile.mkdtemp()) / "core", log=lambda s: None)
    core.start()
    await core.connect()
    try:
        await asyncio.sleep(3)                                     # фоновая проверка «умеет ли модель действия»
        n = len(SEEN)
        a, ev = await core.say("Подбери мне плейлист под настроение, штук пять песен.", timeout=120)
        tools = X.tools_of(ev)
        searches = [t for t in tools if t["name"] == "web_search"]
        ok(len(searches) == 1, f"одинаковый поиск в одном ходе выполнен один раз (вызовов модели: {len(SEEN) - n})")
        ok(any(x["final"] and not x["tools"] for x in SEEN[n:]), "последний шаг — без действий, только ответ")
        ok("Lumineers" in a and a.strip() != "Готово.", f"ответ по сути, а не «Готово.»: {a[:80]}")
        a, _ = await core.say("Сколько стоит чай в магазине у дома?", timeout=120)
        ok(BLANK["n"] == 2 and "100 рублей" in a, f"пустой ответ модели — повтор, а не «нет доступа к инструментам»: {a[:80]}")
    finally:
        await core.stop()


asyncio.run(main())

import codework as C  # noqa: E402
d = Path(tempfile.mkdtemp())
proj, work = d / "proj", d / "task" / "рабочая копия"
proj.mkdir()
work.mkdir(parents=True)
(work / "only_in_copy.txt").write_text("правка задачи")
cw = C.CodeWork(proj, work)
r = cw.run(f"cd {proj} && cat only_in_copy.txt")
ok("правка задачи" in r and "заменён на рабочую копию" in r, f"«cd путь_проекта» — команда уходит в рабочую копию")
ok(cw.path(f"{proj}/only_in_copy.txt") == (work / "only_in_copy.txt").resolve(), "путь исходного проекта в файле — тоже в рабочую копию")
ok(cw.path(str(work / "only_in_copy.txt")) == (work / "only_in_copy.txt").resolve(), "полный путь рабочей копии — понимается")
srv.shutdown()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
print("ALL OK" if not fails else "")
sys.exit(1 if fails else 0)
