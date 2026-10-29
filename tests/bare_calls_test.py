"""Модель без родных функций (Ollama «chat», функции текстом) пишет вызовы голым JSON без <tool_call>:
{"tool_name": "write_file", "arguments": {...}} — иногда в ```json```, иногда с полным путём к папке задачи.
Раньше это считалось «рассуждением», и задача падала: «модель так и не взялась за дело». Теперь:
- такие ответы — действия (stream_events отдаёт "tools", текста нет — нечего озвучивать);
- обычный текст и JSON, который не вызов известной функции, — текстом, как был;
- задача доводится до конца, файл с полным путём ложится в outputs/ папки задачи."""
import json, os, re, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ.update(DEWIAR_WORKSPACE=str(tmp / "ws"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_CONFIG_DIR=str(tmp / "cfg"))
sys.path.insert(0, str(ROOT / "core"))
import llm  # noqa: E402
from workspace import Workspace  # noqa: E402
import tasks as TK  # noqa: E402
import tools as T  # noqa: E402

FAILS = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        FAILS.append(m)


def wait_for(cond, sec=30):
    end = time.time() + sec
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


REPLY = {"next": None}
STEP = {"n": 0}
TASK_DIR = {"p": None}


def script(n):
    full = f"{TASK_DIR['p']}/outputs/news.html"
    return [
        '{"tool_name": "update_plan", "arguments": {"plan": "**Готово, когда:** файл\\n\\n- [ ] написать\\n- [ ] показать"}}',
        '```json\n{"tool_name": "write_file", "arguments": {"path": "' + full + '", "content": "<!doctype html><html><body>Новости</body></html>"}}\n```',
        '{"tool_name": "show", "arguments": {"path": "' + full + '"}}',
        '{"tool_name": "finish", "arguments": {"summary": "Презентация готова: outputs/news.html"}}',
    ][min(n, 3)]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        sysm = body["messages"][0]["content"]
        ok_tools = "tools" not in body                       # режим «функции текстом» — поле tools не шлём
        if REPLY["next"] is not None:
            out = REPLY["next"]
        elif "<tools>" not in sysm:
            out = '{"verdict": "ok"}' if sysm.startswith("Ты — строгий") else '{"lessons": []}' if sysm.startswith("Ты — аналитик") else "# Блокнот\n"
        else:
            out = script(STEP["n"]); STEP["n"] += 1
        if not ok_tools:
            FAILS.append("поле tools ушло на сервер, хотя функции описаны текстом")
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for i in range(0, len(out), 17):                      # кусками, как настоящий поток
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': out[i:i + 17]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
PROF = {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "chat", "tools_mode": "prompt"}
SPEC = [{"name": "show", "description": "показать", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]


def run(reply):
    REPLY["next"] = reply
    ev = list(llm.stream_events(PROF, "sys", [{"role": "user", "content": "x"}], tools=SPEC))
    REPLY["next"] = None
    return "".join(v for k, v in ev if k == "text"), next((v for k, v in ev if k == "tools"), [])


# ---------- 1. разбор ----------
txt, calls = run('{"tool_name": "show", "arguments": {"path": "a.html"}}')
ok([(c["name"], c["args"]) for c in calls] == [("show", {"path": "a.html"})] and txt == "", f"голый JSON — действие, текста нет: {calls} {txt!r}")
txt, calls = run('```json\n{"tool": "show", "parameters": {"path": "b"}}\n```')
ok(calls and calls[0]["args"] == {"path": "b"}, f"в ```json``` и с ключами tool/parameters: {calls}")
txt, calls = run('Привет! Сейчас покажу.')
ok(txt == "Привет! Сейчас покажу." and not calls, "обычный текст — текстом")
txt, calls = run('{"temperature": 12, "city": "Минск"}')
ok(txt == '{"temperature": 12, "city": "Минск"}' and not calls, "JSON, который не вызов, — текстом, ничего не потеряно")
txt, calls = run('{"tool_name": "rm_rf", "arguments": {}}')
ok(not calls, "неизвестная функция не выполняется")
# Эва в 21:00 сказала вслух «{"name": "money_report", …}</tool_call>»: открывающего тега нет, после вызова — сочинённый ответ
txt, calls = run('{"name": "show", "arguments": {"path": "d"}}</tool_call>Сегодня вы потратили 500 рублей.')
ok([(c["name"], c["args"]) for c in calls] == [("show", {"path": "d"})] and txt == "",
   f"голый JSON и одинокий </tool_call> — вызов; ни JSON, ни сочинённый до результата ответ не звучат: {calls} {txt!r}")
txt, calls = run('Сейчас посмотрю. {"name": "show", "arguments": {"path": "e"}}</tool_call> Итог: ноль.')
ok([c["name"] for c in calls] == ["show"] and txt.strip() == "Сейчас посмотрю." and "{" not in txt and "tool_call" not in txt,
   f"вызов голым JSON посреди текста — вырезан и выполнен: {calls} {txt!r}")
txt, calls = run('Готово.</tool_call>')
ok(txt == "Готово." and not calls, f"одинокий </tool_call> в тексте не звучит: {txt!r}")
txt, calls = run('<tool_call>\n{"tool_name": "show", "arguments": {"path": "c"}}\n</tool_call>')
ok(calls and calls[0]["name"] == "show", f"в тегах с ключом tool_name: {calls}")
ok(llm.parse_tool_call('{"tool_name": "use_skill", "arguments": {"name": "slides"}}') == {"name": "use_skill", "args": {"name": "slides"}},
   "parse_tool_call понимает tool_name")
# экзамен 14.10 (Qwen в облаке): перевод строки в ключе и имя функции первой строкой — раньше «не разобран» и пустой ответ
ok(llm.parse_tool_call('\n{"\n\nname": "code_run", "arguments": {"command": "python3 -m pytest -q"}}\n') == {"name": "code_run", "args": {"command": "python3 -m pytest -q"}},
   "вызов с переводом строки внутри ключа «name»")
ok(llm.parse_tool_call('\n{"\n\nname": "code_diff", "arguments": {}}\n') == {"name": "code_diff", "args": {}}, "то же без аргументов")
ok(llm.parse_tool_call('\n{"use_skill", "name": "letters"}\n') == {"name": "use_skill", "args": {"name": "letters"}},
   "{\"use_skill\", \"name\": …} — имя функции первой строкой")
txt, calls = run('<tool_call>\n{"\n\nname": "show", "arguments": {"path": "q"}}\n</tool_call>')
ok(calls and calls[0]["args"] == {"path": "q"} and not txt.strip(), f"в потоке — тоже вызов, а не пустой ответ: {calls} {txt!r}")


# ---------- 2. задача, как у пользователя ----------
class FakeS:
    def get(self, *k): return {} if k[0] == "tools" else None


class Host:
    def __init__(self):
        self.workspace = Workspace(None); self.s = FakeS(); self.toolbox = T.Toolbox(self.s, None); self.toolbox.hooks["workspace"] = self.workspace
        self.said, self.stopping = [], threading.Event()
    def persona_name(self): return "Лира"
    def memory(self): return ""
    def llm_profile(self): return dict(PROF)
    def max_steps(self): return 30
    def code_auto_run(self): return True
    def confirm(self, q): return True
    def ask_user(self, t, q): pass
    def announce(self, text): self.said.append(text)
    def push(self, *a, **k): pass
    def emit(self, *a, **k): pass


tm = TK.TaskManager(Host())
orig = TK.TaskManager._run


def hook(self, t, *a, **k):
    TASK_DIR["p"] = str(t.dir)
    return orig(self, t, *a, **k)


TK.TaskManager._run = hook
t = tm.create("Презентация Новости", "Сделай презентацию по главным новостям")
ok(wait_for(lambda: t.status in ("done", "failed"), 60), f"задача закончилась: {t.status} {t.error or ''}")
ok(t.status == "done", f"готово, а не «модель так и не взялась за дело» ({t.error})")
f = t.dir / "outputs" / "news.html"
ok(f.is_file() and "Новости" in f.read_text(encoding="utf-8"), "файл с полным путём лёг в outputs/ папки задачи")
ok(not any(p.name == "home" for p in t.dir.iterdir()), "нет вложенной копии пути home/…/outputs внутри папки задачи")
j = (t.dir / "журнал.md").read_text(encoding="utf-8")
ok("без действий" not in j and "💭 {" not in j, "в журнале — действия, а не «рассуждения» с JSON")

srv.shutdown()
print("ALL OK" if not FAILS else f"ошибок: {len(FAILS)}")
sys.exit(1 if FAILS else 0)
