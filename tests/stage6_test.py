"""Этап 6 «Глаза» через живое ядро: экран, камера (с подтверждением и отказом), картинка из виджета, формат для Claude."""
import asyncio, base64, io, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import websockets
from PIL import Image
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "core"))
from llm import tool_turn_messages
PORT, LLM_PORT = 8791, 8129
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({"stt": {"engine": "vosk"}, "tts": {"engine": "espeak"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "llm": {"active": "t", "profiles": {"t": {"name": "Mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1"}}}}))
xv = subprocess.Popen(["Xvfb", ":83", "-screen", "0", "1920x1080x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(1)
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DISPLAY=":83", DEWIAR_NO_PORTAL="1", DEWIAR_FAKE_CAMERA="1")
env.pop("WAYLAND_DISPLAY", None)
sil = tmp / "s.wav"; subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "60", "-sample_fmt", "s16", str(sil)])
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env, stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events = []
async def run():
    for _ in range(100):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try: ws = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError: continue
    await ws.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]
    async def reader():
        async for raw in ws:
            e = json.loads(raw)
            if e["type"] not in ("level", "reply"): events.append(e)
    rd = asyncio.ensure_future(reader())
    async def call(cmd, **a):
        n[0] += 1; await ws.send(json.dumps({"id": n[0], "cmd": cmd, "args": a}))
    async def wait_answer(sub, limit=20):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if any(e["type"] == "answer" and e.get("done") and sub in e["text"] for e in events): return True
            await asyncio.sleep(0.1)
    async def wait_ev(pred, limit=20):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if any(pred(e) for e in events): return True
            await asyncio.sleep(0.1)
    await wait_ev(lambda e: e["type"] == "status" and e["status"].get("input"))
    await call("say", text="Что у меня на экране?")
    await wait_answer("1600x900")
    await call("say", text="Посмотри в камеру, что у меня в руке")
    await wait_ev(lambda e: e["type"] == "confirm")
    await call("confirm_answer", ok=True)
    await wait_answer("1280x720")
    n_conf = sum(e["type"] == "confirm" for e in events)
    await call("say", text="Ещё раз глянь в камеру")
    await wait_ev(lambda e: sum(x["type"] == "confirm" for x in events) > n_conf)
    await call("confirm_answer", ok=False)
    await wait_ev(lambda e: e["type"] == "tool" and e.get("status") == "declined")
    await asyncio.sleep(2)
    big = io.BytesIO(); Image.new("RGB", (3000, 2000), (30, 60, 90)).save(big, "PNG")
    await call("say", text="Что это за график?", image="data:image/png;base64," + base64.b64encode(big.getvalue()).decode())
    await wait_answer("1600x1067")
    rd.cancel()
try: asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); xv.terminate(); core.wait(); mock.wait()
log = (tmp / "core.log").read_text()
print("\n".join(l for l in log.splitlines() if any(k in l for k in ("🛠", "Снимок", "Подтверждение", "Traceback", "ERROR"))))
answers = [e["text"] for e in events if e["type"] == "answer" and e.get("done")]
m = tool_turn_messages({"kind": "anthropic"}, "", [{"id": "t1", "name": "look_at_screen", "args": {}}], [{"text": "снимок", "image": "QUJD"}])
o = tool_turn_messages({"kind": "openai"}, "", [{"id": "t1", "name": "look_at_screen", "args": {}}], [{"text": "снимок", "image": "QUJD"}])
checks = {
    "экран: снимок дошёл до модели (1600×900 из 1920×1080)": any("1600x900" in a for a in answers),
    "камера: спросила разрешение и сняла кадр": any("1280x720" in a for a in answers),
    "камера: после отказа кадр не снимался": sum("1280x720" in a for a in answers) == 1 and any(e["type"] == "tool" and e.get("status") == "declined" for e in events),
    "картинка из виджета ужата 3000×2000 → 1600×1067": any("1600x1067" in a and "графи" in a for a in answers),
    "Claude: картинка внутри tool_result": m[1]["content"][0]["content"][1]["type"] == "image",
    "vLLM/OpenAI: картинка отдельным сообщением после tool": o[-1]["role"] == "user" and o[-1]["content"][0]["type"] == "image_url",
}
for k, v in checks.items(): print(("✅" if v else "❌"), k)
sys.exit(0 if all(checks.values()) else 1)
