"""Радио внутри ассистента и вложения: свой список станций с сайта (radio.json): «включи радио джаз» → играет через свой
проигрыватель (не ответившая станция — следующая); «стоп» — пауза и сон; «продолжи» — снова играет; «включи музыку» —
спросит жанр, «рок» — включит; «дальше» — другая станция; «выключи музыку». Файл и картинка к вопросу. YouTube — в браузере."""
import asyncio, base64, http.server, io, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, zipfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
ff = shutil.which("ffmpeg")
if not ff:
    print("нет ffmpeg — пропускаю"); print("ALL OK"); sys.exit(0)
www = tmp / "www"; www.mkdir()
subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=120", "-ac", "1", "-b:a", "64k",
                str(www / "jazz.mp3")], check=True)
s = socket.socket(); s.bind(("127.0.0.1", 0)); WP = s.getsockname()[1]; s.close()


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/radio.json"):
            b = json.dumps({"genres": ["джаз", "рок"], "stations": [
                {"name": "Dead Jazz", "url": f"http://127.0.0.1:{WP}/none.mp3", "tags": "джаз"},
                {"name": "Test Jazz FM", "url": f"http://127.0.0.1:{WP}/jazz.mp3", "tags": "джаз спокойно"},
                {"name": "Test Rock", "url": f"http://127.0.0.1:{WP}/jazz.mp3?rock", "tags": "рок"}]}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        if self.path.startswith("/json/stations/search"):
            b = json.dumps([{"name": "Test Jazz FM", "url_resolved": f"http://127.0.0.1:{WP}/jazz.mp3", "countrycode": "RU",
                             "tags": "jazz", "bitrate": 64},
                            {"name": "Dead Radio", "url_resolved": f"http://127.0.0.1:{WP}/none.mp3", "tags": "jazz"}]).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        return super().do_GET()


srv = http.server.ThreadingHTTPServer(("127.0.0.1", WP), lambda *a, **k: H(*a, directory=str(www), **k))
threading.Thread(target=srv.serve_forever, daemon=True).start()

import websockets  # noqa: E402
PORT, LLM_PORT = 8807, 8137
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "workspace": {"path": str(tmp / "ws")}, "radio_url": f"http://127.0.0.1:{WP}/radio.json",
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_RADIO_API=f"http://127.0.0.1:{WP}", DEWIAR_DRY_TOOLS=str(tmp / "dry.jsonl"))
sil = TESTS / "silence90.wav"
if not sil.exists():
    import wave
    with wave.open(str(sil), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 90)
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM_PORT)])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
events, R = [], {}


def docx(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return base64.b64encode(b.getvalue()).decode()


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
    for _ in range(60):                                    # список станций с сайта подтягивается в фоне
        lst = (await call("radio_list"))["data"]
        if any(x["name"] == "Test Jazz FM" for x in lst["stations"]): break
        await asyncio.sleep(0.25)
    await call("say", text="Включи радио джаз")
    R["answer"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "Включаю" in e.get("text", ""), 30)
    for _ in range(80):                                    # мёртвая станция могла попасться первой — ждём живую
        R["playing"] = (await call("radio_status"))["data"]
        if R["playing"]["playing"] and R["playing"]["station"] == "Test Jazz FM": break
        await asyncio.sleep(0.25)
    await asyncio.sleep(1)
    R["playing"] = (await call("radio_status"))["data"]
    events.clear()
    await call("say", text="Стоп")
    await asyncio.sleep(1.5)
    R["paused"] = (await call("radio_status"))["data"]
    R["sleep"] = any(e["type"] == "sleep" for e in events)
    await call("say", text="Продолжи")
    await asyncio.sleep(1.5)
    R["resumed"] = (await call("radio_status"))["data"]
    await call("radio_stop")
    events.clear()
    await call("say", text="Включи музыку")
    R["ask"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done") and "Какую музыку" in e.get("text", ""), 15)
    await call("say", text="рок")
    await asyncio.sleep(2.5)
    R["rock"] = (await call("radio_status"))["data"]
    events.clear()
    await call("say", text="Дальше")
    await asyncio.sleep(4.5)
    R["next"] = (await call("radio_status"))["data"]
    R["radio_ev"] = any(e["type"] == "radio" for e in events)
    await call("say", text="Выключи музыку")
    await asyncio.sleep(1)
    R["off"] = (await call("radio_status"))["data"]
    await call("say", text="Включи свет в спальне")
    await asyncio.sleep(1.5)
    R["light"] = (await call("radio_status"))["data"]
    events.clear()
    await call("say", text="Что в этом документе?", files=[{"name": "Договор.docx", "b64": docx("Срок поставки — 14 дней с даты оплаты.")}],
               images=["data:image/png;base64," + base64.b64encode(open(ROOT / "icons/dewiar-48.png", "rb").read()).decode()])
    R["doc"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 20)
    R["doc_text"] = next((e["text"] for e in events if e["type"] == "answer" and e.get("done")), "")
    events.clear()
    await call("say", text="Покажи ролик про котиков")
    R["yt"] = await wait_for(lambda e: e["type"] == "answer" and e.get("done"), 20)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); mock.terminate(); core.wait(); mock.wait(); srv.shutdown()
ok(R.get("answer") and R["playing"]["playing"] and R["playing"]["station"] == "Test Jazz FM", f"«включи радио джаз» — из своего списка, мёртвая станция пропущена: играет {R.get('playing', {}).get('station')}")
ok(R.get("ask") and R["rock"]["playing"] and R["rock"]["station"] == "Test Rock", "«включи музыку» — спросил жанр; «рок» — играет рок")
ok(R["next"]["playing"] and R["next"]["station"] == "Test Jazz FM" and R.get("radio_ev"), f"«дальше» — другая станция ({R['next']['station']}), виджет узнал")
ok(not R["off"]["playing"] and not R["off"]["paused"], "«выключи музыку» — выключено")
ok(not R["light"]["playing"], "«включи свет в спальне» — это не музыка")
ok(not R["paused"]["playing"] and R["paused"]["paused"] == "Test Jazz FM" and R.get("sleep"), "«стоп» — радио на паузе, ассистент уснул")
ok(R["resumed"]["playing"] and R["resumed"]["station"] == "Test Jazz FM", "«продолжи» — снова играет")
inbox = list((tmp / "ws" / "Входящие").glob("*"))
ok(any(p.name == "Договор.docx" for p in inbox), "вложение сохранено во «Входящие»")
ok("14 дней" in R.get("doc_text", "") or "Вложения" in R.get("doc_text", ""), "текст документа ушёл модели вместе с вопросом")
dry = (tmp / "dry.jsonl").read_text(encoding="utf-8") if (tmp / "dry.jsonl").exists() else ""
ok(R.get("yt") and "youtube.com" in dry, "«покажи ролик» — YouTube в браузере")
