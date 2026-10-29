"""Новые возможности — в экзамене (просьба 16.10: «новые функции важно уметь проверять»): сценарии timer_count
(таймер, «сколько осталось?», «добавь минуту» — в программе таймер на ~4 минуты), remind_screenshot («напомни завтра
в 10 про это» + картинка — у напоминания вложение), remind_screenshot_model (модель сама ставит напоминание по
сообщению с картинкой — картинка при нём), docs_folder_source (своя папка с документами: ответ из неё и файл-источник
под ответом). Экзамен на модели-заглушке: сценарии проходят, проверки состояния видят то, что надо; телефон — те же
сценарии в EXAM_PHONE и проверка вида таймера и фото у напоминания."""
import asyncio, json, os, sys, threading
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


def text_of(m):
    c = m.get("content")
    return " ".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else str(c or "")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = json.dumps({"data": [{"id": "m"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        sysm = text_of(msgs[0]) if msgs and msgs[0]["role"] == "system" else ""
        last, allm = text_of(msgs[-1]), json.dumps(msgs, ensure_ascii=False)
        calls, out = [], ""
        if sysm.startswith("Ты — строгий и справедливый экзаменатор"):
            out = json.dumps({"score": 8, "why": "по делу"}, ensure_ascii=False)
        elif "обсудить его с Сашей" in allm:
            if msgs[-1]["role"] == "tool" or last.startswith("<tool_response>"):
                out = "Поставила напоминание на завтра в 9:00 — скрин будет прямо в нём."
            else:
                calls = [("set_reminder", {"text": "Обсудить список дел с Сашей", "when": "завтра в 9:00"})]
        elif "плату за аренду офиса" in allm:
            if msgs[-1]["role"] == "tool" or last.startswith("<tool_response>"):
                out = "По файлу «Аренда офиса» — до пятого числа каждого месяца." if "пятого" in allm else "Не нашла."
            else:
                calls = [("search_documents", {"query": "арендная плата"})]
        else:
            out = "Хорошо."
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        if out:
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': out}}]}, ensure_ascii=False)}\n\n".encode())
        for k, (name, args) in enumerate(calls):
            tc = {"index": k, "id": f"c{k}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [tc]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
prof = {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "m", "title": "Заглушка"}
NEW = {"timer_count", "remind_screenshot", "remind_screenshot_model", "docs_folder_source"}
sc = {s["id"]: s for s in json.loads((ROOT / "data/exam/scenarios.json").read_text(encoding="utf-8"))["scenarios"]}
ok(NEW <= set(sc), f"сценарии в экзамене компьютера: {sorted(NEW - set(sc)) or 'все'}")
rep = asyncio.run(X.run_exam(prof, only=NEW, log=print))
R = {r["id"]: r for r in rep["scenarios"]}
for k in sorted(NEW):
    r = R.get(k, {})
    ok(r.get("passed"), f"{k}: {[(c['name'], c['ok'], c.get('detail', '')[:80]) for c in r.get('checks', [])]} · {[s.get('answer', '')[:70] for s in r.get('steps', [])]}")
src_check = [c for c in R.get("docs_folder_source", {}).get("checks", []) if c["name"] == "под ответом — файл-источник"]
ok(src_check and src_check[0]["ok"] and "Аренда офиса" in src_check[0]["detail"], f"проверка источника есть и видит файл: {src_check}")
srv.shutdown()
ex = (ROOT / "android/assets/www/exam.js").read_text(encoding="utf-8")
ok('id: "timer_count"' in ex and 'id: "remind_photo"' in ex and "w.kind && (x.kind" in ex and "(n.att || []).length < w.att" in ex,
   "телефон: те же испытания и проверки (вид таймера, фото при напоминании)")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
