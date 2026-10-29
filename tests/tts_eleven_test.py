"""Голос своим ключом: ElevenLabs (компьютер) и свой сервер голоса с отдельными голосами героинь и героев.
ElevenLabs: голос героя или героини (по умолчанию George и Sarah), ключ — заголовком xi-api-key, звук — PCM 24 кГц потоком
(кусок сети с нечётным числом байт не ломает сэмплы); ошибка сервиса — понятная. Сервер голоса (OpenAI-совместимый):
voice_f / voice_m важнее голоса персонажа. Движок elevenlabs выбирается в настройках и стоит первым в цепочке."""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


SEEN = []
PCM = bytes(range(200)) * 10                 # 2000 байт = 1000 сэмплов


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        SEEN.append({"path": self.path, "key": self.headers.get("xi-api-key"), "auth": self.headers.get("Authorization"), "body": body})
        if "bad" in self.path:
            b = b'{"detail": {"status": "quota_exceeded"}}'
            self.send_response(401); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.send_response(200); self.send_header("Content-Type", "audio/pcm" if "text-to-speech" in self.path else "application/octet-stream")
        self.end_headers()
        for i in range(0, len(PCM), 333):           # нечётные куски
            self.wfile.write(PCM[i:i + 333]); self.wfile.flush()

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
from tts import ElevenTTS, RemoteTTS  # noqa: E402

ElevenTTS.URL = f"http://127.0.0.1:{port}/v1/text-to-speech/{{vid}}/stream"
e = ElevenTTS({"api_key": "sk_test", "model": "eleven_flash_v2_5"})
out = list(e.synth("Привет.", {"_gender": "male"}, 1.0))
audio = b"".join(p for _, p in out)
s = SEEN[-1]
ok("JBFqnCBsd6RMkjVDRZzb/stream" in s["path"] and "output_format=pcm_24000" in s["path"] and s["key"] == "sk_test" and not s["auth"],
   f"герой — George, ключ заголовком xi-api-key: {s['path']}")
ok(audio == PCM and all(r == 24000 and len(p) % 2 == 0 for r, p in out), "звук потоком, сэмплы целые")
list(e.synth("Привет.", {"_gender": "female"}, 1.5))
ok("EXAVITQu4vr4xnSDxMaL" in SEEN[-1]["path"] and SEEN[-1]["body"]["voice_settings"]["speed"] == 1.2 and SEEN[-1]["body"]["model_id"] == "eleven_flash_v2_5",
   f"героиня — Sarah; скорость в пределах ElevenLabs: {SEEN[-1]['body']}")
e2 = ElevenTTS({"api_key": "sk_test", "voice_m": "myVoiceId"})
list(e2.synth("Да.", {"_gender": "male"}))
ok("/myVoiceId/stream" in SEEN[-1]["path"], "свой голос героя из настроек")
e3 = ElevenTTS({"api_key": "sk_test", "voice_m": "bad"})
try:
    list(e3.synth("Да.", {"_gender": "male"})); err = ""
except RuntimeError as x:
    err = str(x)
ok("ElevenLabs ответил 401" in err, f"ошибка сервиса — понятная: {err[:80]}")

r = RemoteTTS({"base_url": f"http://127.0.0.1:{port}/v1", "voice_m": "onyx", "voice_f": ""})
list(r.synth("Привет.", {"_gender": "male", "remote": "aiden"}))
ok(SEEN[-1]["body"]["voice"] == "onyx", f"сервер голоса: голос героев из настроек важнее голоса персонажа: {SEEN[-1]['body']['voice']}")
list(r.synth("Привет.", {"_gender": "female", "remote": "serena"}))
ok(SEEN[-1]["body"]["voice"] == "serena", "голос героинь не задан — свой у персонажа")

# цепочка: выбран ElevenLabs с ключом — он первый
import types  # noqa: E402
import engine as E  # noqa: E402
fake = types.SimpleNamespace(s=types.SimpleNamespace(get=lambda *k: "elevenlabs" if k == ("tts", "engine") else None),
                             tts_remote=e, vosk_tts=types.SimpleNamespace(ready=False, error=""), piper=types.SimpleNamespace(has=lambda v: True),
                             espeak=types.SimpleNamespace(available=lambda: True), _voice_cfg=lambda: {"piper": "ru_RU-denis-medium"})
fake.piper = types.SimpleNamespace(has=lambda v: True)
chain = E.Engine._engines_for_speech(fake)
ok(chain and chain[0] is e and len(chain) == 3, f"цепочка голоса: ElevenLabs, потом Piper и eSpeak на случай сбоя: {[type(x).__name__ for x in chain]}")
srv.shutdown()
print("ALL OK" if not fails else f"ошибок: {len(fails)}")
sys.exit(1 if fails else 0)
