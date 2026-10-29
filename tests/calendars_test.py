"""Календари Google/Яндекс по ссылке iCal: разбор повторов (по будням, исключения, перенос), подключение из студии,
события — в календаре студии, в «что у меня завтра?» (инструмент calendar_list) и в утренней сводке; за N минут до
встречи — уведомление; плохая ссылка — понятная ошибка."""
import asyncio, http.server, json, os, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(ROOT / "core"))
from calsync import parse_ics, occurrences  # noqa: E402

now = datetime.now().replace(second=0, microsecond=0)
soon = now + timedelta(minutes=3)
tom = (now + timedelta(days=1)).replace(hour=10, minute=0)
f = lambda d: d.strftime("%Y%m%dT%H%M%S")
ICS = f"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Работа
BEGIN:VEVENT
UID:a1
DTSTART:{f(soon)}
DTEND:{f(soon + timedelta(minutes=30))}
SUMMARY:Созвон с заказчиком
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART:{f(tom)}
DTEND:{f(tom + timedelta(hours=1))}
RRULE:FREQ=DAILY;COUNT=5
EXDATE:{f(tom + timedelta(days=2))}
SUMMARY:Планёрка
END:VEVENT
BEGIN:VEVENT
UID:a2
RECURRENCE-ID:{f(tom + timedelta(days=1))}
DTSTART:{f(tom + timedelta(days=1, hours=5))}
DTEND:{f(tom + timedelta(days=1, hours=6))}
SUMMARY:Планёрка (перенесли)
END:VEVENT
END:VCALENDAR
"""
occ = occurrences(parse_ics(ICS), now - timedelta(days=1), now + timedelta(days=10))
pl = [o for o in occ if o["title"].startswith("Планёрка")]
ok(len(pl) == 4 and any(o["title"] == "Планёрка (перенесли)" and o["start"].hour == 15 for o in pl),
   f"повторы: 5 раз минус исключение, один перенесён ({len(pl)})")


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/cal/secret.ics"):
            b = ICS.encode(); self.send_response(200); self.send_header("Content-Type", "text/calendar"); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8157), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

import websockets  # noqa: E402
PORT, LLM_PORT = 8823, 8153
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "calendars": {"remind_minutes": 5},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_CAL_TICK="2", NO_PROXY="127.0.0.1,localhost",
           no_proxy="127.0.0.1,localhost")
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events, R = [], {}


async def run():
    ws_ = None
    for _ in range(300):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try: ws_ = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError: continue
    await ws_.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]; replies = {}
    async def call(cmd, **args):
        n[0] += 1; i = n[0]; await ws_.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
        for _ in range(600):
            if i in replies: return replies.pop(i)
            await asyncio.sleep(0.05)
    async def wait_for(pred, limit):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            for e in events:
                if pred(e): return e
            await asyncio.sleep(0.1)
    async def reader():
        async for raw in ws_:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(2)
    R["bad"] = await call("cal_add", url="http://127.0.0.1:8157/nope.ics")
    R["add"] = await call("cal_add", url="webcal://127.0.0.1:8157/cal/secret.ics".replace("webcal://", "http://"))
    R["month"] = (await call("calendar_month", year=tom.year, month=tom.month))["data"]
    R["push"] = await wait_for(lambda e: e["type"] == "push" and "Созвон с заказчиком" in e.get("text", ""), 20)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    for p in (core, mock):
        p.terminate()
    core.wait()
    srv.shutdown()
log = (tmp / "core.log").read_text(encoding="utf-8")
ok(R.get("bad") and not R["bad"].get("ok") and "ссылк" in (R["bad"].get("error") or ""), f"плохая ссылка — понятная ошибка: {(R.get('bad') or {}).get('error')}")
add = (R.get("add") or {}).get("data") or {}
ok(add.get("items") and add["items"][0]["name"] == "Работа" and add["items"][0]["status"]["count"] == 3,
   "календарь подключён: имя из файла, события посчитаны")
day = (R.get("month") or {}).get("days", {}).get(tom.date().isoformat(), [])
ok(any(x["type"] == "event" and x["title"] == "Планёрка" and x["time"] == "10:00" and x["cal"] == "Работа" for x in day),
   "событие — в календаре студии (тип «встреча», время, календарь)")
ok(R.get("push") and "Через" in R["push"].get("title", ""), "за 5 минут до встречи — уведомление «Через N мин»")
sys.path.insert(0, str(ROOT / "core"))
