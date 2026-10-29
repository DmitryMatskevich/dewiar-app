"""Нет устройства вывода (как на Windows без динамиков или по RDP): ловит случай, когда каждый голос «не справляется»,
журнал путается, а человек слышит тишину без объяснений. Проверяет: одна понятная подсказка в виджете, текст ответа
на экране остаётся, следующие фразы не синтезируются зря."""
import asyncio, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import websockets
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()); (tmp / "cfg").mkdir()
PORT, LLM = 8793, 8123
(tmp / "cfg/settings.json").write_text(json.dumps({
    "llm": {"active": "t", "profiles": {"t": {"name": "Mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM}/v1", "disable_thinking": True}}},
    "stt": {"engine": "vosk"}, "tts": {"engine": "espeak"}, "audio": {"sounds": False, "output": "нет-такого-устройства"}}))
fails = []
def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c: fails.append(m)
sil = tmp / "sil.wav"
subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "40", "-sample_fmt", "s16", str(sil)])
# sounddevice без звуковой карты (контейнер): RawOutputStream падает так же, как на Windows без устройства
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_AUDIO_BACKEND="sounddevice", PULSE_SERVER="unix:/nonexistent")
mock = subprocess.Popen([sys.executable, str(TESTS / "mock_llm.py"), str(LLM), "Первая фраза ответа. Вторая фраза ответа. Третья фраза ответа."])
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil)], env=env, stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events = []

async def run():
    tok = None
    for _ in range(100):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            tok = (tmp / "cfg/token").read_text().strip()
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{PORT}"); break
            except OSError:
                continue
    await ws.send(json.dumps({"cmd": "hello", "token": tok}))
    async def reader():
        async for raw in ws:
            events.append(json.loads(raw))
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(3)
    await ws.send(json.dumps({"id": 1, "cmd": "say", "args": {"text": "Расскажи что-нибудь"}}))
    for _ in range(150):
        await asyncio.sleep(0.2)
        if any(e.get("type") == "answer" and e.get("done") for e in events):
            break
    await asyncio.sleep(2)
    rd.cancel()
try:
    asyncio.run(run())
finally:
    core.terminate(); core.wait(10); mock.terminate()
log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
ans = [e for e in events if e.get("type") == "answer" and e.get("done")]
notes = [e.get("status", {}).get("note", "") for e in events if e.get("type") == "status"]
ok(ans and "Вы сказали" in ans[-1].get("text", ""), f"ответ на экране есть, хотя звука нет: {(ans or [{}])[-1].get('text', '')[:60]!r}")
ok(any("Звук не выводится" in n for n in notes), f"в виджете подсказка про вывод звука: {[n for n in notes if n][:2]}")
ok(log.count("Вывод звука не открылся") == 1, f"в журнале — одна понятная запись (а не «синтез не удался» на каждую фразу): {log.count('Вывод звука не открылся')}")
ok("не удался" not in log or "Синтез" not in log.split("Вывод звука")[1][:2000] if "Вывод звука" in log else False, "голоса не перебирались зря")
if fails or os.environ.get("SHOWLOG"):
    print("\n".join(l for l in log.splitlines() if "dewiar" in l)[-4000:])
sys.exit(1 if fails else 0)
