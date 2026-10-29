"""Заметки: «запиши…», «добавь в список покупок…», «запомни, что я…» — сразу, без модели; потом на вопрос по
сохранённому ассистент сам находит запись и отвечает по ней (даже модель без инструментов); поиск в «Памяти»."""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
from capture import parse  # noqa: E402
from recall import Recall  # noqa: E402

C = [("Запиши: пароль от вайфая кот123", ("note", "Пароль от вайфая кот123")),
     ("запиши пароль от почты: qwerty", ("note", "Пароль от почты")),
     ("сделай заметку купить подарок Оле на день рождения", ("note", "Купить подарок Оле на день рождения")),
     ("добавь молоко и хлеб в список покупок", ("list", "Список покупок")),
     ("добавь в список покупок: яйца, сыр", ("list", "Список покупок")),
     ("запомни, что я люблю крепкий чай без сахара", ("fact", None)),
     ("запиши меня к врачу на завтра", None), ("как дела", None)]
bad = []
for q, want in C:
    r = parse(q)
    got = (r["op"], r.get("title") or r.get("name")) if r else None
    if want is None and got is not None or want is not None and (not got or got[0] != want[0] or (want[1] and got[1] != want[1])):
        bad.append(f"{q} → {got}")
ok(not bad, f"{len(C)} фраз «запиши/добавь/запомни» поняты" + ("; не так: " + " | ".join(bad) if bad else ""))

ws = tmp / "ws0"; (ws / "Заметки").mkdir(parents=True); (ws / "Память").mkdir(); (ws / "Дневник").mkdir()
(ws / "Заметки/Пароль от вайфая.md").write_text("# Пароль от вайфая\n\nСеть Дача_5G, пароль кот123\n", encoding="utf-8")
(ws / "Заметки/Рецепт борща.md").write_text("# Рецепт борща\n\nСвёкла, капуста, картошка, морковь. Варить 2 часа.\n", encoding="utf-8")
(ws / "Заметки/Машина.md").write_text("# Машина\n\nСтраховка ОСАГО до 15 марта, номер полиса ХХХ 0123456789.\n", encoding="utf-8")
(ws / "Память/Обо мне.md").write_text("# Обо мне\n\n- Дочь зовут Аня, ей 7 лет\n", encoding="utf-8")
rc = Recall(lambda: ws)
r = rc.search("какой у меня пароль от вайфая?")
ok(r and r[0]["path"] == "Заметки/Пароль от вайфая.md", "«какой пароль от вайфая?» — нашлась нужная заметка")
r = rc.search("до какого числа страховка на машину")
ok(r and r[0]["path"] == "Заметки/Машина.md", "«до какого числа страховка» — по словам в разных формах")
ok(rc.context("как зовут мою дочь") and "Аня" in rc.context("как зовут мою дочь"), "память «Обо мне» тоже ищется")
ok(rc.context("расскажи анекдот про программистов") == "", "вопрос не про записи — подсказки нет, модель не сбиваем")
(ws / "Заметки/Пароль от вайфая.md").write_text("# Пароль от вайфая\n\nНовый пароль: лиса777\n", encoding="utf-8")
os.utime(ws / "Заметки/Пароль от вайфая.md", (time.time() + 5, time.time() + 5))
ok("лиса777" in rc.context("пароль от вайфая"), "изменили заметку — поиск видит новое сразу")

import websockets  # noqa: E402
PORT, LLM_PORT = 8808, 8138
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False},
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
    await asyncio.sleep(1)
    t0 = time.monotonic()
    await call("say", text="Запиши: пароль от вайфая на даче кот123")
    R["note"] = await wait_for(lambda e: e["type"] == "note_saved", 5)
    R["note_fast"] = time.monotonic() - t0 < 3
    R["note_said"] = await wait_for(lambda e: e["type"] == "answer" and e.get("text", "").startswith("Записал"), 5)
    events.clear()
    await call("say", text="добавь молоко и хлеб в список покупок")
    await wait_for(lambda e: e["type"] == "note_saved", 5)
    await call("say", text="добавь в список покупок: яйца, молоко")
    R["list_dup"] = await wait_for(lambda e: e["type"] == "answer" and "яйца" in e.get("text", "") and "молоко" not in e.get("text", ""), 5)
    await call("say", text="запомни, что я люблю крепкий чай без сахара")
    await wait_for(lambda e: e["type"] == "note_saved" and e["kind"] == "fact", 5)
    await asyncio.sleep(0.5)
    events.clear()
    await call("say", text="Какой пароль от вайфая на даче?")
    R["recalled"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "кот123" in e.get("text", ""), 20)
    events.clear()
    await call("say", text="Что мне купить в магазине по списку покупок?")
    R["list_recall"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "молоко" in e.get("text", ""), 20)
    R["search"] = (await call("recall", query="вайфай дача"))["data"]
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait()
notes = tmp / "ws/Заметки"
ok(R.get("note") and R.get("note_fast") and R.get("note_said") and any("вайфая" in p.name for p in notes.glob("*.md")),
   "«запиши: …» — заметка сохранена сразу, без модели, ответ «Записал»")
lst = (notes / "Список покупок.md").read_text(encoding="utf-8") if (notes / "Список покупок.md").exists() else ""
ok(lst.count("- молоко") == 1 and "- хлеб" in lst and "- яйца" in lst and R.get("list_dup"), "список покупок: пункты добавляются, повторы — нет")
mem = (tmp / "ws/Память/Обо мне.md").read_text(encoding="utf-8") if (tmp / "ws/Память/Обо мне.md").exists() else ""
ok("крепкий чай" in mem, "«запомни, что я…» — в память «Обо мне»")
ok(R.get("recalled"), "«какой пароль от вайфая?» — ассистент нашёл заметку и ответил по ней")
ok(R.get("list_recall"), "«что купить по списку?» — ответ по списку покупок")
ok(R.get("search") and "вайфа" in R["search"][0]["path"], "поиск по записям в студии («Память»)")
