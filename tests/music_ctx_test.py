"""Музыка по-человечески: «открой Яндекс Музыку» → приложение; следом «включи электронную музыку» — там же, а не в
YouTube; «нажми на кнопку воспроизведения», «следующий трек» — кнопки плеера Яндекс Музыки без разрешений и кликов;
«дальше» в обычном разговоре — не кнопка плеера; выбор «где включать музыку»; ярлык «Yandex Music» помнит, что открыт."""
import asyncio, json, os, subprocess, sys, tempfile, time, wave
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
tmp = Path(tempfile.mkdtemp())
apps = tmp / "apps"; apps.mkdir()
(apps / "yandex-music.desktop").write_text("[Desktop Entry]\nType=Application\nName=Yandex Music\nName[ru]=Яндекс Музыка\nExec=yandex-music %U\n"
                                           "MimeType=x-scheme-handler/yandexmusic;\n")

# ярлык программы: open_app помнит, что открыта Яндекс Музыка
sys.path.insert(0, str(ROOT / "core"))
os.environ.update(DEWIAR_DRY_TOOLS=str(tmp / "dry0.jsonl"), DEWIAR_APPS_DIR=str(apps))
import tools  # noqa: E402
seen = []
tools.t_open_app({"name": "яндекс музыку"}, {"music_app": seen.append})
ok(seen == ["yandex"], f"«запусти Яндекс Музыку» через модель — помню, что открыта ({seen})")

import websockets  # noqa: E402
PORT = 8816
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({"persona": "dewiar", "stt": {"engine": "vosk"}, "tts": {"engine": "off"}, "ui": {"welcome": "done"},
                                                   "audio": {"sounds": False, "follow_up_seconds": 0}}))
DRY = tmp / "dry.jsonl"
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(PORT), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE_DIR=str(tmp / "ws"), DEWIAR_DRY_TOOLS=str(DRY), DEWIAR_FAKE_YANDEX_APP="1", DEWIAR_APPS_DIR=str(apps))
sil = TESTS / "silence90.wav"
if not sil.exists():
    with wave.open(str(sil), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 90)
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(sil), "--null-audio"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
events, R = [], {}
dry = lambda: [json.loads(l) for l in DRY.read_text(encoding="utf-8").splitlines()] if DRY.exists() else []


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
    async def say(text, wait=1.5):
        events.clear(); before = len(dry()); await call("say", text=text); await asyncio.sleep(wait)
        ans = next((e.get("text", "") for e in events if e["type"] == "answer" and e.get("done")), "")
        return ans, dry()[before:]
    async def reader():
        async for raw in ws:
            e = json.loads(raw)
            if e["type"] == "reply": replies[e["id"]] = e
            elif e["type"] != "level": events.append(e)
    rd = asyncio.ensure_future(reader())
    await asyncio.sleep(1)
    R["further"] = await say("дальше")                         # разговор, а не плеер: ничего не нажимаем
    R["open"] = await say("Открой Яндекс Музыку")
    R["genre"] = await say("Включи электронную музыку")
    R["play"] = await say("Нажми на кнопку воспроизведения")
    R["next"] = await say("Следующий трек")
    R["further2"] = await say("дальше")
    R["explicit"] = await say("Поставь джаз в Яндекс Музыке")
    await call("settings_save", patch={"music": {"service": "youtube"}})
    R["yt"] = await say("Включи спокойную музыку")
    rd.cancel()

try:
    asyncio.run(run())
finally:
    core.terminate(); core.wait()

urls = lambda d: " ".join(" ".join(map(str, x)) for x in d)
ok(not any(x[0] == "media" for x in R["further"][1]), f"«дальше» в разговоре — не кнопка плеера ({R['further'][1]})")
ok("yandexmusic://" in urls(R["open"][1]) and "Яндекс Музык" in R["open"][0], f"«открой Яндекс Музыку» — приложение: {R['open'][0]}")
ok("yandexmusic://search?text=" in urls(R["genre"][1]) and "youtube" not in urls(R["genre"][1]), f"следом «включи электронную музыку» — в Яндекс Музыке, не в YouTube: {R['genre'][0]}")
ok(["media", "play", "yandex"] in R["play"][1], f"«нажми на кнопку воспроизведения» — кнопка плеера Яндекс Музыки, без разрешений ({R['play'][1]})")
ok(["media", "next", "yandex"] in R["next"][1], "«следующий трек» — следующий трек в Яндекс Музыке")
ok(["media", "next", "yandex"] in R["further2"][1], "пока открыта Яндекс Музыка, «дальше» — следующий трек")
ok("yandexmusic://search?text=%D0%B4%D0%B6%D0%B0%D0%B7" in urls(R["explicit"][1]), "«поставь джаз в Яндекс Музыке» — сразу там")
ok("music.youtube.com" in urls(R["yt"][1]), "выбор «YouTube Music» в «Руках» — жанры там")
