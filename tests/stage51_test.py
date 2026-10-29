"""Этап 5.1: редактор навыков, скачивание/установка, каталог, черновик от модели, создание голосом."""
import asyncio, base64, io, json, os, subprocess, sys, tempfile, threading, time, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import websockets
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
PORT, LLM_PORT, CAT_PORT = 8792, 8128, 8155
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({"stt": {"engine": "vosk"}, "tts": {"engine": "espeak"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "llm": {"active": "t", "profiles": {"t": {"name": "Mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1"}}}}))
buf = io.BytesIO(); z = zipfile.ZipFile(buf, "w"); z.writestr("math-tutor/SKILL.md", "---\nname: math-tutor\ndescription: Репетитор по математике для школьника\n---\nОбъясняй по шагам.\n"); z.close()
class C(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/catalog.json":
            b = json.dumps({"skills": [{"name": "math-tutor", "title": "Репетитор по математике", "description": "По шагам", "url": "math-tutor.zip"}]}, ensure_ascii=False).encode()
        else:
            b = buf.getvalue()
        self.send_response(200); self.end_headers(); self.wfile.write(b)
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", CAT_PORT), C).serve_forever, daemon=True).start()
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"))
sil = tmp / "s.wav"; subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "60", "-sample_fmt", "s16", str(sil)])
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env, stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
R, events = {}, []
async def run():
    for _ in range(100):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try: ws = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError: continue
    await ws.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]; replies = {}
    async def reader():
        async for raw in ws:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    async def call(cmd, **a):
        n[0] += 1; i = n[0]; await ws.send(json.dumps({"id": i, "cmd": cmd, "args": a}))
        for _ in range(400):
            if i in replies: return replies.pop(i)
            await asyncio.sleep(0.05)
    async def wait_for(pred, limit):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if any(pred(e) for e in events): return
            await asyncio.sleep(0.1)
    R["draft"] = await call("skill_draft", idea="режим фокуса: тишина и таймер на 25 минут")
    d = R["draft"]["data"]
    R["save"] = await call("skill_save", name=d["name"], description=d["description"], body=d["body"], files={"references/tips.md": "Дыши глубже"})
    R["get"] = await call("skill_get", name="focus-mode")
    R["rename"] = await call("skill_save", name="deep-focus", description=d["description"], body=d["body"] + "\n4. Удачи.", files={"references/tips.md": "Дыши глубже"}, old_name="focus-mode")
    R["zip"] = await call("skill_export", name="deep-focus")
    R["rm"] = await call("skill_remove", name="deep-focus")
    R["reinstall"] = await call("skill_install", zip_b64=R["zip"]["data"]["zip_b64"])
    R["bad"] = await call("skill_save", name="x", description="нормальное описание", body="b", files={"../../evil.sh": "rm"})
    R["builtin"] = await call("skill_save", name="morning-brief", description="Моя утренняя сводка, короче обычной", body="Только дата и погода.", old_name="")
    R["cat"] = await call("skills_catalog", url=f"http://127.0.0.1:{CAT_PORT}/catalog.json")
    R["catinst"] = await call("skill_install", url=R["cat"]["data"][0]["url"])
    await call("say", text="Научись чайной паузе")
    await wait_for(lambda e: e["type"] == "confirm", 15)
    await call("confirm_answer", ok=True)
    await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "tea-time" in e["text"], 15)
    R["final"] = await call("skills")
    rd.cancel()
try: asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait()
names = {s["name"]: s for s in (R.get("final") or {}).get("data", [])}
zf = zipfile.ZipFile(io.BytesIO(base64.b64decode(R["zip"]["data"]["zip_b64"]))) if R.get("zip", {}).get("ok") else None
checks = {
    "модель составила черновик навыка": R["draft"]["ok"] and R["draft"]["data"]["name"] == "focus-mode",
    "навык сохранён с файлом": R["save"]["ok"] and R["get"]["data"]["files"] == {"references/tips.md": "Дыши глубже"},
    "переименование сохраняет файлы": R["rename"]["ok"] and (tmp / "ws/Навыки/deep-focus/references/tips.md").exists() and not (tmp / "ws/Навыки/focus-mode").exists(),
    "скачанный .zip — полный навык": zf is not None and sorted(zf.namelist()) == ["deep-focus/SKILL.md", "deep-focus/references/tips.md"],
    "скачанный навык устанавливается обратно": R["reinstall"]["ok"] and R["reinstall"]["data"]["name"] == "deep-focus",
    "опасный путь файла отвергнут": not R["bad"]["ok"],
    "правка встроенного навыка — своя копия": R["builtin"]["ok"] and (tmp / "ws/Навыки/morning-brief/SKILL.md").exists() and not names.get("morning-brief", {}).get("builtin", True),
    "каталог с сайта открылся": R["cat"]["ok"] and R["cat"]["data"][0]["title"] == "Репетитор по математике",
    "навык из каталога установлен": R["catinst"]["ok"] and "math-tutor" in names,
    "«научись…» голосом: спросил и создал навык": any(e["type"] == "confirm" and "tea-time" in e["text"] for e in events) and "tea-time" in names,
}
for k, v in checks.items(): print(("✅" if v else "❌"), k)
sys.exit(0 if all(checks.values()) else 1)
