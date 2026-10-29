"""Свой сервер Ollama (родной /api/chat), модель «chat» пишет вызов функции текстом
в формате GLM (<tool_call>имя<arg_key>…</arg_key><arg_value>…</arg_value></tool_call>), а иногда рассуждает
по-английски без тега <think> и заканчивает «</think>». Ловит «модель вернула пустой ответ», спрятанные кнопки
и рассуждения вслух. Проверяет: функция выполняется, рассуждения не видны и не звучат, код не читается вслух,
длинный ответ вслух обрезается, итог задачи — по-русски и не «готово», если ничего не сделано."""
import asyncio, json, os, re, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import websockets
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


SEEN = []


class Ollama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_stream(self, pieces):
        self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
        for p in pieces:
            self.wfile.write((json.dumps({"model": "chat", "message": {"role": "assistant", "content": p}, "done": False},
                                         ensure_ascii=False) + "\n").encode()); self.wfile.flush(); time.sleep(0.01)
        self.wfile.write((json.dumps({"model": "chat", "message": {"role": "assistant", "content": ""}, "done": True,
                                      "done_reason": "stop"}) + "\n").encode())

    def do_GET(self):
        body = json.dumps({"models": [{"name": "chat"}]}).encode() if self.path.startswith("/api/tags") else b"{}"
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/api/chat":
            self.send_response(404); self.end_headers(); return
        msgs = req["messages"]
        last = msgs[-1]
        SEEN.append(req)
        sysmsg = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
        if "Переведи текст на русский" in sysmsg:
            return self._send_stream(["Готово: проверил USB-устройства, всё на месте."])
        if "ЗАДАЧА: Пустая болтовня" in sysmsg:
            return self._send_stream(["Let me reconstruct where I am. I need to check the USB hub. Let me think more.</think>"])
        if "ЗАДАЧА: Проверь USB" in sysmsg:
            done = sum(1 for m in msgs if m["role"] == "tool" or "<tool_response>" in str(m.get("content")))
            if done == 0:
                return self._send_stream(["<tool_call>run_command\n<arg_key>command</arg_key>\n<arg_value>echo usb-ok</arg_value>\n</tool_call>"])
            return self._send_stream(["<tool_call>finish\n<arg_key>summary</arg_key>\n<arg_value>Done. I checked the USB devices "
                                      "with lsusb and everything is in place, nothing else is needed.</arg_value>\n</tool_call>"])
        user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
        if last["role"] == "tool" or "<tool_response>" in str(last.get("content")):
            res = str(last.get("content"))
            if "длинн" in user:
                return self._send_stream(["Вот подробности. " + "Проверьте кабель и питание хаба, это важно. " * 40])
            return self._send_stream([f"Готово, открыла. ({res[:30]})"])
        if "который" in user.lower() and "час" in user.lower():                     # проверка «умеет ли функции»
            return self._send_stream(["<tool_call>get_time\n</tool_call>"])
        if "яндекс" in user.lower():
            return self._send_stream(["Okay, the user wants Yandex Music. I should call yandex_music with the query.\n",
                                      "</think>", "<tool", "_call>yandex", "_music\n<arg_key>query</arg_key>\n",
                                      "<arg_value>Элла</arg_value>\n</tool_call>"])
        if "команд" in user.lower():
            return self._send_stream(["Сейчас проверю.", "<tool_call>run_command\n<arg_key>command</arg_key>\n",
                                      "<arg_value>echo \"=== USB ===\"; lsusb; dkms status 2>/dev/null</arg_value>\n</tool_call>"])
        if "код" in user.lower():
            return self._send_stream(["Выполните так:\n```bash\nsudo dkms autoinstall\nsudo reboot\n```\nПотом проверьте мониторы."])
        if "длинн" in user:
            return self._send_stream(["<tool_call>battery_status\n</tool_call>"])
        return self._send_stream(["Привет! Всё хорошо."])


LLM, PORT = free(), free()
srv = ThreadingHTTPServer(("127.0.0.1", LLM), Ollama)
threading.Thread(target=srv.serve_forever, daemon=True).start()
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
dry = tmp / "launched.jsonl"
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "eva", "ui": {"welcome": "done"},
    "llm": {"active": "home", "profiles": {"home": {"name": "Dewiar Server", "kind": "ollama", "base_url": f"http://127.0.0.1:{LLM}/v1",
                                                    "model": "chat", "disable_thinking": True}},
            "tools_modes": {f"http://127.0.0.1:{LLM}/v1|chat": "none"}},          # запомненный ошибочный итог проверки: «не умеет»
    "stt": {"engine": "vosk"}, "tts": {"engine": "espeak"},
    "audio": {"sounds": False, "follow_up_seconds": 0},
    "tools": {"enabled": {"run_command": True}, "confirm": {"run_command": False}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_DRY_TOOLS=str(dry), DEWIAR_WORKSPACE=str(tmp / "ws"),
           DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8")
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio", "--debug"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
R = {}


async def run():
    ws = None
    for _ in range(150):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError:
                continue
    await ws.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    events, n = [], [0]

    async def reader():
        async for raw in ws:
            e = json.loads(raw)
            if e.get("type") != "level":
                events.append(e)
    rd = asyncio.ensure_future(reader())

    async def ask(text, limit=25):
        events.clear(); n[0] += 1
        await ws.send(json.dumps({"id": n[0], "cmd": "say", "args": {"text": text}}))
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if any(e["type"] == "answer" and e.get("done") for e in events) or any(e["type"] == "error" for e in events):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(1.5)
        return list(events)

    await asyncio.sleep(4)                                      # проверка инструментов заново (запомненное «none» забывается)
    R["ym"] = await ask("Поищи в яндексе Эллу, пожалуйста")
    R["cmd"] = await ask("Выполни команду проверки USB")
    R["code"] = await ask("Покажи код для починки")
    R["long"] = await ask("Дай длинный ответ про мониторы")
    async def call(cmd, **args):
        n[0] += 1; i = n[0]
        await ws.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
        for _ in range(200):
            for e in list(events):
                if e.get("type") == "reply" and e.get("id") == i:
                    return e.get("data")
            await asyncio.sleep(0.05)
    events.clear()
    t1 = await call("task_create", title="Пустая болтовня", goal="Проверь мониторы")
    t2 = await call("task_create", title="Проверь USB", goal="Проверь USB-устройства")
    for _ in range(120):
        await asyncio.sleep(0.5)
        d1, d2 = await call("task_get", id=t1["id"]), await call("task_get", id=t2["id"])
        if d1 and d2 and d1.get("status") not in ("running", "paused", "queued") and d2.get("status") not in ("running", "paused", "queued"):
            break
    R["t1"], R["t2"] = d1, d2
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); core.wait(10); srv.shutdown()

log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
if os.environ.get("SHOWLOG"):
    print("\n".join(l for l in log.splitlines() if "dewiar" in l and "DEBUG" not in l)[-6000:])
launched = dry.read_text(encoding="utf-8") if dry.exists() else ""
st = json.loads((tmp / "cfg/settings.json").read_text(encoding="utf-8"))
ans = lambda evs: next((e.get("text", "") for e in reversed(evs) if e["type"] == "answer" and e.get("done")), "")
said = lambda evs: [e for e in evs if e["type"] == "tool"]
ym = R.get("ym", [])
ok("yandexmusic://search?text=" in launched or "music.yandex.ru/search" in launched, "вызов функции в формате GLM выполнен: Яндекс Музыка открыта с поиском")
ok("Okay" not in ans(ym) and "think" not in ans(ym) and ans(ym).startswith("Готово"), f"рассуждение по-английски не показано: {ans(ym)!r}")
ok("пустой ответ" not in log and "без текста" not in log, "нет «модель вернула пустой ответ»")
ok((st["llm"].get("tools_modes") or {}).get(f"http://127.0.0.1:{LLM}/v1|chat") in ("native", "prompt"),
   f"старое «не умеет действия» перепроверено: {(st['llm'].get('tools_modes') or {})}")
cmd = R.get("cmd", [])
runs = [e for e in said(cmd) if e.get("name") == "run_command"]
ok(runs and runs[-1].get("detail", "").startswith("echo") and runs[-1].get("status") in ("done", "error"),
   f"ход работы: команда видна в виджете целиком ({runs[-1].get('detail', '')[:40] if runs else '—'})")
spoken = [l.split("🗣 ", 1)[1] for l in log.splitlines() if "🗣 " in l]
code_said = " ".join(x for x in spoken if "перезагруз" in x or "на экране" in x or "dkms" in x or "Выполните" in x)
ok("dkms" not in " ".join(spoken) and "Команды и код — на экране" in code_said, f"код вслух не читается: {code_said[:120]!r}")
ok(not any("lsusb" in x or "===" in x for x in spoken), "команда вслух не диктуется")
ok("🔇 Ответ длинный" in log, "длинный ответ вслух обрезан, остальное — на экране")
ok(len(ans(R.get("long", []))) > 1500, "на экране — весь длинный ответ")
t1, t2 = R.get("t1") or {}, R.get("t2") or {}
ok(t1.get("status") == "failed" and re.search("рассужда|думала", t1.get("error") or ""), f"задача без единого действия — не «готово», а честная ошибка: {t1.get('status')} / {(t1.get('error') or '')[:80]} {(t1.get('journal') or '')[-400:] if t1.get('status') != 'failed' else ''}")
ok(t2.get("status") == "done" and (t2.get("result") or "").startswith("Готово: проверил"), f"задача с действиями выполнена, итог по-русски: {(t2.get('result') or '')[:60]!r}")
ok("echo usb-ok" in launched or "usb-ok" in json.dumps(t2, ensure_ascii=False), "команда задачи (формат GLM) выполнена")
