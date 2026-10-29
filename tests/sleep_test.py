"""«Стоп / хватит / спасибо» — не вопрос модели, а сон до имени (как «Алиса, хватит»); второе нажатие гасит ожидание,
нажатие во время ответа — замолчать и уснуть."""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
import websockets  # noqa: E402
PORT, LLM_PORT = 8805, 8135
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "espeak"}, "audio": {"sounds": False, "follow_up_seconds": 6},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1")
sil = TESTS / "silence90.wav"
if not sil.exists():
    import wave
    with wave.open(str(sil), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 90)
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            if any(pred(e) for e in events): return True
            await asyncio.sleep(0.1)
    async def reader():
        async for raw in ws:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    await wait_for(lambda e: e["type"] == "status" and e["status"].get("input"), 60)
    await call("say", text="Как дела?")
    await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 20)
    await asyncio.sleep(0.5); events.clear()
    for phrase in ["Stop.", "Хватит, спасибо!", "всё, отбой"]:
        await call("say", text=phrase)
        await asyncio.sleep(1.2)
    R["no_llm"] = not any(e["type"] == "answer" for e in events)
    R["slept"] = sum(1 for e in events if e["type"] == "sleep") == 3
    R["idle"] = [e["state"] for e in events if e["type"] == "state"][-1:] == ["idle"]
    events.clear()
    R["tap1"] = (await call("ptt"))["data"]
    R["tap2"] = (await call("ptt"))["data"]
    R["idle2"] = [e["state"] for e in events if e["type"] == "state"][-1:] == ["idle"]
    await call("say", text="Расскажи историю")
    R["voice"] = await wait_for(lambda e: e["type"] == "state" and e["state"] == "speaking", 20)
    events.clear()
    R["tap3"] = (await call("ptt"))["data"]
    await asyncio.sleep(1.5)
    R["states3"] = [e["state"] for e in events if e["type"] == "state"]
    R["idle3"] = R["states3"][-1:] == ["idle"]
    await call("say", text="Стоп, лучше расскажи про космос")
    R["question"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "космос" in e.get("text", ""), 20)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait()
ok(R.get("no_llm"), "«Stop.», «Хватит, спасибо!», «всё, отбой» — модели не отправляются")
ok(R.get("slept") and R.get("idle"), "после них — сон до имени, а не «слушаю»")
ok(R.get("tap1") == "listening" and R.get("tap2") == "sleep" and R.get("idle2"), "нажатие — слушаю, второе нажатие — перестал слушать")
if not R.get("voice"):
    print("нет синтеза речи (espeak) — «нажатие во время ответа» пропускаю")
else:
  ok(R.get("tap3") == "sleep" and R.get("idle3"), "нажатие во время ответа — замолчал и уснул"
   + ("" if R.get("tap3") == "sleep" and R.get("idle3") else f" ({R.get('tap3')}, {R.get('states3')})"))
ok(R.get("question"), "«Стоп, лучше расскажи про космос» — это вопрос, на него отвечает")
