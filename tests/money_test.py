"""Деньги и календарь: «купил кофе за восемь пятьдесят», «получил зарплату три тысячи», «сколько я потратил за неделю?»,
«отмени последнюю трату» — без модели; итоги по категориям; файл «Финансы/ГГГГ-ММ.md»; календарь месяца собирает
деньги, напоминания (с повторами), заметки, дневник; Telegram тоже записывает."""
import asyncio, datetime as dt, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
from money import Money, parse, words_to_number  # noqa: E402

C = [("купил кофе за 8 рублей", ("expense", 8, "Кафе и рестораны")), ("купил кофе за восемь пятьдесят", ("expense", 8.5, "Кафе и рестораны")),
     ("потратил 50 на бензин", ("expense", 50, "Транспорт")), ("купил 2 билета в кино за 30", ("expense", 30, "Развлечения")),
     ("получил зарплату три тысячи", ("income", 3000, "Зарплата")), ("заработал 200 долларов на проекте", ("income", 200, "Подработка")),
     ("вчера купила продукты на 75,50", ("expense", 75.5, "Продукты")), ("оплатил коммуналку полторы тысячи", ("expense", 1500, "Дом и связь")),
     ("получил 5 сообщений", None), ("купил хлеб", None), ("получил письмо от Пети", None), ("купи хлеба", None)]
bad = []
for q, want in C:
    r = parse(q)
    got = (r["kind"], r["amount"], r["category"]) if r and r["op"] == "add" else None
    if got != want:
        bad.append(f"{q} → {got}")
ok(not bad, f"{len(C)} фраз о деньгах поняты" + ("; не так: " + " | ".join(bad) if bad else ""))
ok(parse("сколько я потратил за неделю")["op"] == "query" and parse("какие доходы в сентябре")["kind"] == "income"
   and parse("отмени последнюю трату")["op"] == "undo", "вопросы о деньгах и «отмени последнюю трату»")
ok(words_to_number("сто двадцать рублей пятьдесят копеек")[0] == 120.5 and words_to_number("3к")[0] == 3000, "суммы словами и «3к»")


class WS:
    def __init__(self, root): self.root = root
    def ensure(self): self.root.mkdir(parents=True, exist_ok=True); return self.root


m = Money(tmp / "m.json", WS(tmp / "ws0"), lambda: "BYN")
today = dt.date.today().isoformat()
m.add("expense", 8.5, None, "Кафе и рестораны", "кофе"); m.add("expense", 40, None, "Продукты", ""); m.add("income", 3000, None, "Зарплата", "")
m.add("expense", 10, "USD", "Другое", "подписка")
sm = m.summary(today, today)
byn = next(c for c in sm["by_currency"] if c["currency"] == "BYN")
ok(byn["income"] == 3000 and byn["expense"] == 48.5 and byn["balance"] == 2951.5 and byn["expense_cats"][0]["name"] == "Продукты"
   and len(sm["by_currency"]) == 2, "итоги: доходы, расходы, остаток, категории, другие валюты отдельно")
md = (tmp / "ws0/Финансы" / f"{today[:7]}.md").read_text(encoding="utf-8")
ok("Остаток" in md and "кофе" in md, "«Финансы/ГГГГ-ММ.md» в папке ассистента — для чтения и Obsidian")
ok(Money(tmp / "m.json").summary()["count"] == 4, "записи сохраняются на диск")

import websockets  # noqa: E402
PORT, LLM_PORT = 8812, 8142
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False}, "money": {"currency": "BYN"},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"))
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
    await call("say", text="Купил кофе за восемь пятьдесят")
    R["saved"] = await wait_for(lambda e: e["type"] == "money_saved" and e.get("amount") == 8.5, 10)
    R["said"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "Записал расход" in e.get("text", ""), 10)
    await call("say", text="Получил зарплату три тысячи")
    await wait_for(lambda e: e["type"] == "money_saved" and e.get("amount") == 3000, 10)
    events.clear()
    await call("say", text="Сколько я потратил за неделю?")
    R["week"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "8,5" in e.get("text", ""), 10)
    await call("reminder_add", text="Позвонить Лёше", at=(dt.datetime.now() + dt.timedelta(days=1)).strftime("%Y-%m-%d 15:00"))
    await call("reminder_add", phrase="каждый день в 9 утра зарядка")
    (tmp / "ws/Заметки").mkdir(parents=True, exist_ok=True); (tmp / "ws/Заметки/Идеи.md").write_text("# Идеи", encoding="utf-8")
    now = dt.datetime.now()
    R["cal"] = (await call("calendar_month", year=now.year, month=now.month))["data"]
    events.clear()
    await call("say", text="Отмени последнюю трату")
    R["undo"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "Удалил" in e.get("text", ""), 10)
    R["left"] = (await call("money_list"))["data"]
    r = await call("money_add", kind="expense", amount=12, category="Транспорт", note="такси", date=now.date().isoformat())
    R["added"] = r.get("data") or {}
    R["bad"] = await call("money_add", kind="expense", amount=-5)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    for p in (core, mock):
        p.terminate()
    core.wait()
ok(R.get("saved") and R.get("said"), "«купил кофе за восемь пятьдесят» — записано без модели, ответил одной фразой, виджет показал")
ok(R.get("week"), "«сколько я потратил за неделю?» — ответ голосом с суммой")
days = (R.get("cal") or {}).get("days", {})
flat = [x for v in days.values() for x in v]
reps = [x for x in flat if x["type"] == "reminder" and "зарядка" in x["title"].lower()]
ok(any(x["type"] == "money" for x in flat) and any(x["type"] == "note" and x["title"] == "Идеи" for x in flat)
   and len(reps) >= 1 and (R["cal"].get("money") or {}).get("count") == 2, "календарь месяца: деньги, заметки, напоминания (повтор — каждый день)")
ok(R.get("undo") and len(R.get("left") or []) == 1 and R["left"][0]["kind"] == "income", "«отмени последнюю трату» — удалена последняя запись")
ok(R.get("added", {}).get("category") == "Транспорт" and not R["bad"].get("ok"), "студия: добавить запись; отрицательная сумма — ошибка")
