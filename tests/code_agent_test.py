"""Агент по коду (codework.py, tasks.py): правка прощает отступы и подсказывает похожий кусок; синтаксис проверяется
сразу после правки; подсказка, если pytest нет; карта проекта и команда тестов; итог не принимается, пока тесты
проекта падают (модель-заглушка торопится с finish — получает вывод тестов, чинит и сдаёт)."""
import asyncio, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
import codework as C  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


d = Path(tempfile.mkdtemp())
w = d / "p"
(w / "tests").mkdir(parents=True)
(w / "calc.py").write_text("class Calc:\n    def add(self, a, b):\n        return a - b\n\n    def mul(self, a, b):\n        return a * b\n")
(w / "tests/test_calc.py").write_text("import unittest\nfrom calc import Calc\n\n\nclass T(unittest.TestCase):\n"
                                      "    def test_add(self):\n        self.assertEqual(Calc().add(2, 3), 5)\n")
cw = C.CodeWork(w, w)
r = cw.edit("calc.py", "def add(self, a, b):\nreturn a - b", "def add(self, a, b):\nreturn a + b")
ok("без учёта отступов" in r and "        return a + b" in (w / "calc.py").read_text(), f"правка без отступов — найдена и выровнена: {r}")
try:
    cw.edit("calc.py", "return a * c", "x")
    ok(False, "не найденный кусок — ошибка")
except C.CodeError as e:
    ok("похожий кусок" in str(e) and "return a * b" in str(e), f"не нашёл — показывает похожий кусок с номерами строк")
try:
    cw.edit("calc.py", "return", "x")
    ok(False, "неоднозначный кусок — ошибка")
except C.CodeError as e:
    ok("строки 3, 6" in str(e), f"кусок встречается дважды — названы строки: {e}")
r = cw.edit("calc.py", "        return a * b", "        return a *")
ok("SyntaxError" in r and "строка 6" in r, f"сломал синтаксис — видит сразу: {r[-120:]}")
cw.edit("calc.py", "        return a *", "        return a * b")
r = cw.write("data.json", "{\"a\": 1,}")
ok("JSON не разбирается" in r, "JSON проверяется после записи")
ok(not cw.edit("calc.py", "return a * b", "return b * a").count("⚠"), "правильная правка — без предупреждений")
r = C.run_hint("python3 -m pytest -q", "/usr/bin/python3: No module named pytest", 1, w)
ok("unittest discover" in r, "нет pytest — подсказка, как запустить тесты")
ov = C.overview(w)
ok("calc.py: Calc, add, mul" in ov or "calc.py: Calc" in ov, f"карта проекта: классы и функции")
ok("Тесты: " in ov and ("pytest" in ov or "unittest" in ov), f"карта проекта: команда тестов ({ov.splitlines()[-1]})")
(w / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
ok(C.test_command(w).endswith(("pytest -q", "-q")), "Python-тесты важнее package.json")
w2 = d / "js"
w2.mkdir()
(w2 / "package.json").write_text(json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}))
ok(C.test_command(w2) is None, "заглушка npm test — не команда тестов")

# ---------- откат правок ----------
cw2 = C.CodeWork(d / "u", d / "u" / "рабочая копия")
(d / "u" / "рабочая копия").mkdir(parents=True)
(cw2.work / "a.py").write_text("x = 1\n")
cw2.edit("a.py", "x = 1", "x = 2")
cw2.write("b.py", "y = 1\n")
cw2.edit("a.py", "x = 2", "x = (")
r = cw2.undo(2)
ok((cw2.work / "a.py").read_text() == "x = 2\n" and not (cw2.work / "b.py").exists() and "Отменено правок: 2" in r,
   f"откат двух правок: файл как был, созданный — удалён ({r.splitlines()[0]})")
cw2.undo(5)
ok((cw2.work / "a.py").read_text() == "x = 1\n" and "нечего" in cw2.undo(), "откат до начала, дальше — «нечего»")

# ---------- фоновый процесс: сервер, проверка, вывод, остановка ----------
import socket  # noqa: E402
s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); port = s_.getsockname()[1]; s_.close()
(cw2.work / "index.html").write_text("<h1>привет</h1>")
r = cw2.start_bg(f"python3 -m http.server {port} --bind 127.0.0.1", wait=1.5)
ok("p1: работает" in r, f"сервер запущен в фоне: {r[:60]}")
r = cw2.run(f"python3 -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{port}/index.html').read().decode())\"", 20)
ok("привет" in r, "задача проверяет свой сервер запросом")
ok("GET /index.html" in cw2.process("output", "p1"), "вывод сервера виден")
ok("p1: работает" in cw2.process("status"), "список процессов")
cw2.process("stop", "p1")
import time as _time  # noqa: E402
_time.sleep(0.5)
ok("p1: завершён" in cw2.process("status"), "остановлен")
cw2.start_bg("sleep 300", wait=0.5)
pid = cw2._procs()[-1]["pid"]
cw2.stop_all()
_time.sleep(0.3)
ok(not C._alive({"pid": pid}), "в конце задачи фоновые процессы гасятся сами")

# ---------- итог не принимается, пока тесты падают ----------
import exam as X  # noqa: E402
STEPS = [("finish", {"summary": "Готово (не проверял)."}),              # торопится — проверка вернёт вывод тестов
         ("code_edit", {"path": "stats.py", "old": "return sum(xs) / (len(xs) - 1)", "new": "return sum(xs) / len(xs)"}),
         ("code_edit", {"path": "stats.py", "old": "return (s[mid] + s[mid + 1]) / 2", "new": "return (s[mid - 1] + s[mid]) / 2"}),
         ("finish", {"summary": "Исправил среднее и медиану, тесты проходят."})]
pos, SEEN, BLANKS = [0], [], [0]


def text_of(m):
    c = m.get("content")
    return " ".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else str(c or "")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = b'{"data": [{"id": "m"}]}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        sysm = text_of(msgs[0])
        SEEN.append(json.dumps(msgs, ensure_ascii=False))
        calls, out = [], ""
        if sysm.startswith("Ты — строгий и справедливый экзаменатор"):
            out = '{"score": 8, "why": "ок"}'
        elif "ЗАДАЧА:" in sysm and pos[0] == 2 and BLANKS[0] < 2:     # посреди работы — два пустых ответа подряд
            BLANKS[0] += 1
        elif "ЗАДАЧА:" in sysm:
            calls = [STEPS[min(pos[0], len(STEPS) - 1)]]
            pos[0] += 1
        elif "проверь итог" in sysm.lower() or "самопровер" in sysm.lower() or "критик" in sysm.lower():
            out = "OK"
        else:
            out = "Хорошо."
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        if out:
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': out}}]}, ensure_ascii=False)}\n\n".encode())
        for k, (name, args) in enumerate(calls):
            tc = {"index": k, "id": f"c{pos[0]}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [tc]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
prof = {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "m", "title": "Заглушка"}
rep = asyncio.run(X.run_exam(prof, only={"code_bugfix"}, log=lambda s: None))
r = rep["scenarios"][0]
ok(r["passed"], f"задача сдана после возврата итога: {[(c['name'], c['ok']) for c in r['checks']]} {r.get('task')}")
ok(any("Тесты проекта не проходят — итог не принят" in s for s in SEEN), "поспешный finish вернулся с выводом тестов")
ok(BLANKS[0] == 2 and "↻ Модель ответила пустотой" in (r.get("task") or {}).get("journal", "") + json.dumps(r, ensure_ascii=False)
   or (BLANKS[0] == 2 and r["passed"]), "пустой ответ модели посреди задачи — продолжает, а не проваливается")
ok(any("КАРТА ПРОЕКТА" in s and "stats.py: mean, median" in s for s in SEEN), "в подсказке задачи — карта проекта")
ok("🧪 Проверка перед итогом: тесты проходят" in (r.get("task") or {}).get("journal", "") or r["passed"], "в журнале — тесты прошли")
srv.shutdown()
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
print("ALL OK" if not fails else "")
sys.exit(1 if fails else 0)
