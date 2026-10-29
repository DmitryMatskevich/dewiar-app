"""Созвон: другая программа (здесь — «Zoom», копия pacat) пишет с микрофона → ядро видит созвон и сообщает виджету
(«🎧 Созвон — слушаю только по кнопке»); сам Dewiar, регуляторы громкости и мониторы — не созвон; кончился — как обычно.
Нужен PulseAudio/PipeWire; нет — запускаем свой pulseaudio, не получилось — пропуск (ALL OK)."""
import asyncio, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(ROOT / "core"))


def pa_ok():
    return subprocess.run(["pactl", "info"], capture_output=True).returncode == 0


if not shutil.which("pacat") or not shutil.which("pactl"):
    print("нет pacat/pactl — пропускаю\nALL OK"); sys.exit(0)
if not pa_ok() and shutil.which("pulseaudio"):
    subprocess.run(["pulseaudio", "-D", "-n", "--exit-idle-time=-1", "--load=module-native-protocol-unix",
                    "--load=module-null-sink sink_name=t_out"], capture_output=True)
    time.sleep(1.5)
if not pa_ok():
    print("нет звукового сервера — пропускаю\nALL OK"); sys.exit(0)
mod = subprocess.run(["pactl", "load-module", "module-null-source", "source_name=t_mic"], capture_output=True, text=True).stdout.strip()
time.sleep(0.5)
from meeting import mic_users  # noqa: E402

zoom = tmp / "zoom"
shutil.copy(shutil.which("pacat"), zoom)
mine = subprocess.Popen(["pacat", "--record", "-d", "t_mic"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
ok(mic_users() == [], "pacat, регуляторы громкости, сам Dewiar — не созвон")
# OBS пишет видео с микрофоном — не созвон (отзыв 17.10: «включил OBS — Лира стала глухой»)
obs = tmp / "obs"
shutil.copy(shutil.which("pacat"), obs)
rec = subprocess.Popen([str(obs), "--record", "-d", "t_mic"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)
ok(mic_users() == [], f"OBS записывает видео с микрофоном — не созвон: {mic_users()}")
rec.terminate()
import meeting as _M  # noqa: E402
ok(all(_M.RECORDERS.search(x) for x in ("obs64.exe", "obs-studio", "com.obsproject.Studio", "simplescreenrecorder", "kooha"))
   and not any(_M.RECORDERS.search(x) for x in ("zoom", "Telegram", "obsidian", "chrome")),
   "программы записи (OBS и др.) — не созвон и на Windows; Zoom, Telegram, браузер — созвон")

import websockets  # noqa: E402
PORT, LLM_PORT = 8819, 8149
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({
    "persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "audio": {"sounds": False, "follow_up_seconds": 0},
    "llm": {"active": "m", "profiles": {"m": {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{LLM_PORT}/v1", "model": "chat"}}}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT),
           DEWIAR_ASSUME_AEC="1", DEWIAR_WORKSPACE_DIR=str(tmp / "ws"))
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
events, R = [], {}
call_p = []


async def run():
    ws_ = None
    for _ in range(300):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try: ws_ = await websockets.connect(f"ws://127.0.0.1:{PORT}", max_size=None); break
            except OSError: continue
    await ws_.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))

    async def wait_for(pred, limit):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            for e in events:
                if pred(e): return e
            await asyncio.sleep(0.1)

    async def reader():
        async for raw in ws_:
            e = json.loads(raw)
            if e["type"] not in ("reply", "level"): events.append(e)
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(2)
    call_p.append(subprocess.Popen([str(zoom), "--record", "--client-name=ZOOM VoiceEngine", "-d", "t_mic"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    R["on"] = await wait_for(lambda e: e["type"] == "status" and (e["status"].get("meeting") or {}).get("apps") == ["Zoom"], 10)
    events.clear()
    call_p[0].terminate()
    R["off"] = await wait_for(lambda e: e["type"] == "status" and "meeting" in e["status"] and not e["status"]["meeting"], 10)
    rd.cancel()

try:
    asyncio.run(run())
finally:
    for p in [core, mine] + call_p:
        p.terminate()
    core.wait()
    if mod:
        subprocess.run(["pactl", "unload-module", mod], capture_output=True)
log = (tmp / "core.log").read_text(encoding="utf-8")
ok(R.get("on") and "Созвон (Zoom)" in log, "Zoom взял микрофон — ядро видит созвон: имя не слушаю, говорить — по кнопке")
ok(R.get("off") and "Созвон закончился" in log, "созвон кончился — слушаю как обычно")
