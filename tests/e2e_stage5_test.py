"""Этап 5 через живое ядро: импорт/экспорт по WebSocket, навык и MCP-инструмент в разговоре."""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import websockets
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
PORT, LLM_PORT = 8795, 8127
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({"stt": {"engine": "vosk"}, "tts": {"engine": "espeak"},
                                                   "audio": {"sounds": False, "follow_up_seconds": 0}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1")
sil = tmp / "sil.wav"; subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "90", "-sample_fmt", "s16", str(sil)])
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env, stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
IMPORT = {"dewiar": 1, "name": "Файл от администратора",
          "llm": {"active": "srv", "profiles": {"srv": {"name": "Сервер компании", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "api_key": "sk-secret"}}},
          "mcp": {"servers": {"notes": {"type": "stdio", "command": sys.executable, "args": [str(TESTS / "mcp_test_server.py")]}}}}
events, R = [], {}

async def run():
    for _ in range(100):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try: ws = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError: continue
    await ws.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]; replies = {}
    async def call(cmd, **args):
        n[0] += 1; i = n[0]; await ws.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
        for _ in range(300):
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
    await wait_for(lambda e: e["type"] == "status" and e["status"].get("input"), 20)
    R["preview"] = await call("config_preview", data=IMPORT)
    R["apply"] = await call("config_apply")
    await wait_for(lambda e: e["type"] == "mcp" and e["status"].get("notes", {}).get("status") == "ok", 15)
    R["export"] = await call("config_export")
    await call("say", text="Сделай утреннюю сводку")
    await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "Результат" in e["text"], 15)
    await call("say", text="Запиши заметку про хлеб")
    await wait_for(lambda e: e["type"] == "confirm", 20)
    await call("confirm_answer", ok=True)
    await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "записано" in e["text"], 15)
    rd.cancel()

try: asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait()
log = (tmp / "core.log").read_text()
print("\n".join(l for l in log.splitlines() if any(k in l for k in ("🛠", "↳", "MCP", "Навыки", "Traceback", "ERROR"))))
answers = [e["text"] for e in events if e["type"] == "answer" and e.get("done")]
pv, exp = R.get("preview", {}).get("data") or {}, (R.get("export") or {}).get("data") or {}
checks = {
    "предпросмотр импорта показал модель и предупредил о программе MCP": any("Сервер компании" in l for l in pv.get("lines", [])) and pv.get("warnings"),
    "импорт применён: активна модель из файла": (R.get("apply") or {}).get("data", {}).get("llm", {}).get("active") == "srv",
    "MCP-сервер из файла подключился": any(e["type"] == "mcp" and e["status"].get("notes", {}).get("status") == "ok" for e in events),
    "экспорт без ключа": exp.get("llm", {}).get("profiles", {}).get("srv", {}).get("api_key") == "",
    "модель загрузила навык и получила инструкции": any("Собери короткую утреннюю сводку" in a for a in answers),
    "MCP-инструмент спросил разрешение": any(e["type"] == "confirm" and "notes" in e["text"] for e in events),
    "MCP-инструмент выполнен после «да»": any("записано, всего 1" in a for a in answers),
}
for k, v in checks.items(): print(("✅" if v else "❌"), k)
sys.exit(0 if all(checks.values()) else 1)
