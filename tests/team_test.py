"""Команда: Агент (главный) сам передаёт вопрос специалисту (код — Кодеру, поздравление — Лире), после ответа — снова Агент;
выключенная передача и разговор с не-главным — без передачи; галерея специалистов добавляет нового в команду."""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
from tts import cyr_only  # noqa: E402
ok(cyr_only("Dewiar — это GPT-4o!") == "девиар — это гпт-4о!" or "девиар" in cyr_only("Dewiar — это GPT-4o!").lower(),
   f"латиница для Vosk TTS — русскими буквами: «{cyr_only('Dewiar — это GPT-4o!')}»")
import websockets  # noqa: E402
PORT, LLM_PORT = 8809, 8139
(tmp / "cfg").mkdir()
(tmp / "cfg/personas").mkdir()                       # команда как раньше: Кодер, Джарвис, Эва — добавлены из «Готовых»
for _pid in ['agent', 'jarvis', 'eva']:
    (tmp / "cfg/personas" / f"{_pid}.json").write_text('{"template": true}')
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
    await call("say", text="Напиши скрипт на питоне, который считает файлы в папке")
    R["h1"] = await wait_for(lambda e: e["type"] == "handoff", 5)
    R["a1"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 15)
    R["back1"] = await wait_for(lambda e: e["type"] == "persona" and e["persona"] == "dewiar", 5)
    events.clear()
    await call("say", text="Напиши поздравление маме с днём рождения")
    R["h2"] = await wait_for(lambda e: e["type"] == "handoff", 5)
    await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 15)
    await asyncio.sleep(0.5)
    events.clear()
    await call("say", text="Сколько будет два плюс два?")
    await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 15)
    R["no_route_general"] = not any(e["type"] == "handoff" for e in events)
    await call("settings_save", patch={"team": {"route": False}})
    events.clear()
    await call("say", text="Напиши скрипт для бэкапа")
    await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 15)
    R["off"] = not any(e["type"] == "handoff" for e in events)
    await call("settings_save", patch={"team": {"route": True}})
    await call("persona_set", id="eva")
    events.clear()
    await call("say", text="Напиши скрипт для бэкапа")
    await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 15)
    R["not_main"] = not any(e["type"] == "handoff" for e in events)
    await call("persona_set", id="dewiar")
    g = (await call("gallery"))["data"]
    R["gallery"] = len(g) >= 8 and all(x.get("role") and x.get("skills") for x in g)
    r = (await call("gallery_add", id="gaston"))["data"]
    R["added"] = r["id"] == "gaston" and any(x["id"] == "gaston" and x["installed"] for x in r["gallery"])
    events.clear()
    await call("say", text="Дай рецепт борща")
    R["h3"] = await wait_for(lambda e: e["type"] == "handoff", 5)
    R["hello"] = (await call("hello"))
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait()
ok(R.get("h1") and R["h1"]["to"] == "agent" and R.get("a1") and R.get("back1"), "«напиши скрипт…» — Агент передал Кодеру, тот ответил, снова Агент")
ok(R.get("h2") and R["h2"]["to"] == "lira", "«напиши поздравление…» — Лире")
ok(R.get("no_route_general"), "обычный вопрос Агент отвечает сам")
ok(R.get("off"), "передача выключена в студии — отвечает сам")
ok(R.get("not_main"), "говорите с Эвой — Эва и отвечает (передаёт только главный)")
ok(R.get("gallery") and R.get("added"), "галерея: 8 специалистов с ролями; «Добавить» — Гастон в команде")
ok(R.get("h3") and R["h3"]["to"] == "gaston", "«дай рецепт борща» — шеф-повару Гастону")
