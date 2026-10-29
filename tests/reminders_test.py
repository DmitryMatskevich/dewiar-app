"""Напоминания: фразы понимаются без модели (разовые, повторы, будильник, таймер), будильник звенит до «Готово»,
«отложи на 5 минут» голосом, пропущенное, повтор переставляется, студия добавляет/меняет/удаляет."""
import asyncio, json, os, subprocess, sys, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)

# ---------- разбор фраз ----------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
from when import describe_repeat, next_time, parse  # noqa: E402
NOW = datetime(2026, 10, 1, 10, 0)          # четверг
CASES = [
    ("Напомни мне через 10 минут выключить чайник", "01.10 10:10", "Выключить чайник", ""),
    ("напомни через полчаса проверить почту", "01.10 10:30", "Проверить почту", ""),
    ("напомни через час 20 забрать сына", "01.10 11:20", "Забрать сына", ""),
    ("напомни мне через двадцать пять минут снять пиццу", "01.10 10:25", "Снять пиццу", ""),
    ("Напомни завтра в 9 позвонить маме.", "02.10 09:00", "Позвонить маме", ""),
    ("напомни в 3 часа дня оплатить счёт", "01.10 15:00", "Оплатить счёт", ""),
    ("напомни в половине восьмого выйти из дома", "01.10 19:30", "Выйти из дома", ""),
    ("напомни в пятницу в 10 сдать отчёт", "02.10 10:00", "Сдать отчёт", ""),
    ("напомни 5 октября в 12 поздравить Олю", "05.10 12:00", "Поздравить Олю", ""),
    ("напомни 05.10 в 18:00 забрать посылку", "05.10 18:00", "Забрать посылку", ""),
    ("напомни на завтра купить молоко", "02.10 09:00", "Купить молоко", ""),
    ("напомни сегодня вечером полить цветы", "01.10 19:00", "Полить цветы", ""),
    ("напоминай каждый день в 8 выпить таблетки", "02.10 08:00", "Выпить таблетки", "каждый день в 8:00"),
    ("напоминай по будням в 9:15 про планёрку", "02.10 09:15", "Про планёрку", "по будням в 9:15"),
    ("напоминай каждые 2 часа размяться", "01.10 12:00", "Размяться", "каждые 2 ч"),
    ("напоминай по понедельникам и средам в 19 про спортзал", "05.10 19:00", "Про спортзал", "по пн, ср в 19:00"),
    ("напомни каждый месяц 5 числа оплатить интернет", "05.10 09:00", "Оплатить интернет", "каждый месяц 5-го в 9:00"),
    ("разбуди меня в 7 утра", "02.10 07:00", "Будильник", ""),
    ("поставь будильник на 6:30", "02.10 06:30", "Будильник", ""),
    ("таймер на 5 минут", "01.10 10:05", "Таймер", ""),
]
bad = []
for q, at, text, rep in CASES:
    r = parse(q, NOW)
    got = (f"{r['at']:%d.%m %H:%M}", r["text"], describe_repeat(r["repeat"])) if r else None
    if got != (at, text, rep):
        bad.append(f"{q} → {got}")
ok(not bad, f"{len(CASES)} фраз поняты без модели" + ("; не так: " + " | ".join(bad) if bad else ""))
ok(parse("напомни купить хлеб", NOW) is None and parse("как дела", NOW) is None, "без времени или не просьба — решает модель")
r = parse("Каждую пятницу в 16:30 напомни СДЕЛАТЬ БЭКАП", NOW)
ok(r and r["repeat"] == {"kind": "weekly", "days": [4], "time": "16:30"} and r["text"] == "СДЕЛАТЬ БЭКАП"
   and parse("Как напомнить себе о встрече", NOW) is None and parse("мама сказала напомни ей", NOW) is None,
   "«каждую пятницу в 16:30 напомни…» (время впереди) — тоже без модели; обычные фразы с «напомни» — нет")
ok(next_time({"kind": "weekly", "days": [0, 1, 2, 3, 4], "time": "09:15"}, datetime(2026, 10, 2, 9, 15)) == datetime(2026, 10, 5, 9, 15),
   "по будням: после пятницы — понедельник")
ok(next_time({"kind": "monthly", "day": 31, "time": "10:00"}, datetime(2026, 10, 31, 11, 0)) == datetime(2026, 11, 30, 10, 0),
   "31-е число в ноябре — 30-е")

# ---------- живое ядро ----------
import websockets  # noqa: E402
PORT, LLM_PORT = 8807, 8137
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "espeak"}, "audio": {"sounds": True, "follow_up_seconds": 6},
    "reminders": {"ring_minutes": 0.25},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"))
sil = TESTS / "silence90.wav"
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events, R = [], {}


async def run():
    ws = None
    for _ in range(300):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try: ws = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError: continue
    await ws.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]; replies = {}
    async def call(cmd, **args):
        n[0] += 1; i = n[0]; await ws.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
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
        async for raw in ws:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(1)
    # 1. голосом/текстом — без модели, сразу подтверждение и карточка
    t0 = time.monotonic()
    await call("say", text="Напомни мне через 3 секунды выключить чайник")
    e = await wait_for(lambda e: e["type"] == "reminder_set", 5)
    R["fast"] = e and e["item"]["text"] == "Выключить чайник" and time.monotonic() - t0 < 3
    R["confirm"] = await wait_for(lambda e: e["type"] == "answer" and e.get("text", "").startswith("Хорошо, напомню"), 5)
    # 2. сработало — окно, звонок, голос; «отложи на 5 минут»
    a = await wait_for(lambda e: e["type"] == "alarm", 15)
    R["alarm"] = a and a["text"] == "Выключить чайник" and a.get("window") is True
    R["alarm_get"] = (await call("alarm_get")).get("data")
    await asyncio.sleep(1.5)
    events.clear()
    await call("say", text="отложи на 5 минут")
    d = await wait_for(lambda e: e["type"] == "alarm_done", 8)
    R["snooze"] = d and d["result"] == "snooze"
    items = (await call("reminders"))["data"]
    sn = [x for x in items if x["text"] == "Выключить чайник"]
    R["snoozed_at"] = sn and abs((datetime.fromisoformat(sn[0]["at"]) - datetime.now()).total_seconds() - 300) < 15
    # 3. таймер и кнопка «Готово»
    events.clear()
    await call("say", text="таймер на 2 секунды")
    a = await wait_for(lambda e: e["type"] == "alarm", 10)
    R["timer"] = a and a["kind"] == "timer"
    r = await call("alarm_ack", id=a["id"] if a else "")
    R["ack"] = r.get("data", {}).get("result") == "ack" and await wait_for(lambda e: e["type"] == "alarm_done" and e["result"] == "ack", 3)
    # 4. «хватит» во время звонка — выключить
    events.clear()
    await call("reminder_add", phrase="через 2 секунды проверить духовку")
    await wait_for(lambda e: e["type"] == "alarm", 10)
    await asyncio.sleep(1)
    await call("say", text="хватит")
    R["stop_word"] = await wait_for(lambda e: e["type"] == "alarm_done" and e["result"] == "ack", 5)
    # 5. никто не ответил — «пропущено»
    events.clear()
    await call("reminder_add", phrase="через 1 секунду полить цветы")
    R["missed"] = await wait_for(lambda e: e["type"] == "alarm_done" and e["result"] == "missed", 30)
    # 6. повтор: после звонка переставляется на следующий раз
    r = await call("reminder_add", phrase="каждые 2 минуты пить воду")
    rid = r["data"]["id"]
    R["rep_text"] = r["data"]["repeat_text"]
    await call("reminder_update", id=rid, patch={"at": (datetime.now() + timedelta(seconds=2)).isoformat(timespec="seconds")})
    a = await wait_for(lambda e: e["type"] == "alarm" and e["id"] == rid, 10)
    await call("alarm_ack", id=rid)
    items = (await call("reminders"))["data"]
    it = [x for x in items if x["id"] == rid]
    R["repeat_next"] = bool(a) and it and 100 < (datetime.fromisoformat(it[0]["at"]) - datetime.now()).total_seconds() < 125
    # 7. студия: пауза, изменить словами, удалить
    r = await call("reminder_update", id=rid, patch={"paused": True})
    R["paused"] = r["data"]["paused"]
    r = await call("reminder_update", id=rid, patch={"text": "Пить воду!", "when": "каждый день в 10"})
    R["edited"] = r["data"]["text"] == "Пить воду!" and r["data"]["repeat_text"] == "каждый день в 10:00"
    R["parse_hint"] = (await call("reminder_parse", phrase="по будням в 8:30 планёрка"))["data"]
    R["md"] = (tmp / "ws/Напоминания.md").read_text(encoding="utf-8")
    R["cancel"] = (await call("reminder_cancel", id=rid))["data"]
    R["bad_add"] = (await call("reminder_add", phrase="когда-нибудь потом"))
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait()
ok(R.get("fast") and R.get("confirm"), "«напомни через 3 секунды…» — поставлено сразу, без модели, с подтверждением")
ok(R.get("alarm") and R.get("alarm_get", {}).get("text") == "Выключить чайник", "сработало: окно-будильник с текстом")
ok(R.get("snooze") and R.get("snoozed_at"), "«отложи на 5 минут» голосом — отложено ровно на 5 минут")
ok(R.get("timer") and R.get("ack"), "таймер и кнопка «Готово»")
ok(R.get("stop_word"), "«хватит» во время звонка — будильник выключен")
ok(R.get("missed"), "никто не ответил — звонок выключился сам, «пропущено»")
ok(R.get("rep_text") == "каждые 2 мин" and R.get("repeat_next"), "повтор: после звонка — следующий раз через 2 минуты")
ok(R.get("paused") and R.get("edited"), "студия: пауза и «изменить» словами («каждый день в 10»)")
ok(R.get("parse_hint") and "по будням" in R["parse_hint"]["repeat_text"], "подсказка под полем: как поняли фразу")
ok("Пить воду!" in R.get("md", "") and "каждый день" in R.get("md", ""), "Напоминания.md в папке ассистента — с повторами")
ok(R.get("cancel") and not R.get("bad_add", {}).get("ok"), "удаление; непонятное время — понятная ошибка")
