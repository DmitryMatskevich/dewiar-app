"""Суфлёр, ступень 2 (core/prompter.py + core/speaker.py) без ядра: ядро — заглушка, модель — своя заглушка по HTTP,
распознавание — подставной текст к каждой фразе, голоса — espeak-ng («ru» — вы, «ru+f3» — собеседница).
Запомнить голос (четыре фразы) → очная встреча: одна дорожка микрофона, кто говорит — по голосу; собеседница спросила
и вы молчите — подсказка выделяется («Начните так»); «ну, э-э, как бы, вот» — подсказка; пауза — тихая подсказка;
онлайн-встреча: чужой голос в микрофоне отбрасывается. Модель голоса (28 МБ) — из ~/.cache/dewiar-test или с GitHub."""
import json, os, shutil, subprocess, sys, tempfile, threading, time, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ["DEWIAR_STATE_DIR"] = str(tmp / "st")
os.environ["DEWIAR_CONFIG_DIR"] = str(tmp / "cfg")
sys.path.insert(0, str(ROOT / "core"))
import prompter as P  # noqa: E402
import speaker as S  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


# ---------- без звука ----------
ok(P.stalling("ну э как бы вот") and P.wants_hint("ну вот значит короче мы подумаем", "me"), "«ну, э-э, как бы, вот» — человек подбирает слова")
ok(not P.stalling("как это сделать в срок") and not P.wants_hint("Да, давайте так и сделаем", "me"), "обычная фраза — не повод")
ok("пауза" in P.Prompter._ask_line("pause", "") and "замешкался" in P.Prompter._ask_line("stuck", "Сколько стоит?"), "у пауз и заминок свои просьбы к модели")

# ---------- модель голоса ----------
cache = Path.home() / ".cache/dewiar-test" / S.MODEL_NAME
sp = S.Speaker()
if cache.is_file():
    sp.path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cache, sp.path)
t0 = time.time()
sp.ensure()
if not cache.is_file():
    cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sp.path, cache)
ok(sp.ready(), f"модель голоса на месте ({time.time() - t0:.1f} с)")


def say(text, voice, name):
    subprocess.run(["espeak-ng", "-v", voice, "-s", "150", "-w", str(tmp / "r.wav"), text], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(tmp / "r.wav"), "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                    str(tmp / name)], check=True)
    w = wave.open(str(tmp / name))
    return w.readframes(w.getnframes())


TEXT = {}


def me(t, i):
    p = say(t, "ru", f"me{i}.wav"); TEXT[p] = t; return p


def her(t, i):
    p = say(t, "ru+f3", f"her{i}.wav"); TEXT[p] = t; return p


# ---------- заглушки: модель и ядро ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user = body["messages"][-1]["content"]
        if "пауза" in user:
            out = "Спросите, когда им удобно начать пилот\n• предложите две даты"
        elif "замешкался" in user:
            out = "Цена зависит от числа мест — назовите базовую\n• 4800 за 30 мест"
        else:
            out = "4800 за 30 мест\n• внедрение две недели"
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for i in range(0, len(out), 16):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': out[i:i + 16]}}]}, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush(); time.sleep(0.01)
        self.wfile.write(b"data: [DONE]\n\n")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PROF = {"name": "mock", "kind": "openai", "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "m"}


class STT:
    def transcribe(self, pcm):
        return TEXT.get(pcm, "")


class Eng:
    def __init__(self):
        self.events = []
        self.mic_voice_t = 0.0
        self.s = type("S", (), {"active_llm": staticmethod(lambda: dict(PROF)), "get": staticmethod(lambda *k: None)})()

    def emit(self, type_, **kw):
        self.events.append(dict(kw, type=type_))

    def current_stt(self):
        return STT()

    def set_state(self, s):
        pass


def wait(pred, t=15):
    for _ in range(int(t * 20)):
        m = [e for e in eng.events if pred(e)]
        if m:
            return m
        time.sleep(0.05)
    return []


eng = Eng()
pr = P.Prompter(eng)
pr._speaker = sp
try:
    pr.start(place="live")
    ok(False, "очная встреча без голоса не начинается")
except ValueError as e:
    ok("голос" in str(e), f"очная встреча без голоса не начинается: {e}")

# ---------- запомнить голос ----------
r = pr.enroll_start()
ok(len(r["phrases"]) == 4, "четыре фразы для записи голоса")
ok(pr.enrolling is not None, "идёт запись голоса — микрофон отдаётся суфлёру")
pr.feed_me(me("Да", 99))                                         # коротко — не образец
for i, t in enumerate(P.ENROLL_PHRASES):
    pr.feed_me(me(t, i))
done = wait(lambda e: e["type"] == "prompter" and e.get("kind") == "voice" and e.get("step") == "done")
ok(bool(done) and done[0]["n"] == 4, f"голос запомнен по четырём фразам: {done[:1]}")
ok(pr.voice_info()["has"] and S.OWNER_FILE.is_file() and "vec" in S.OWNER_FILE.read_text(), "отпечаток — только вектор, звука нет")
ok(not list((tmp / "st").rglob("*.wav")), "записи голоса на диске нет")

# ---------- очная встреча ----------
P.STUCK_S = 2.0
pr.start(title="Вживую", goal="Продать пилот", place="live")
ok(pr.meeting["place"] == "live" and "по голосу" in pr.status, f"очная встреча: {pr.status}")
pr.feed_me(her("Добрый день, рада познакомиться", 1))
pr.feed_me(me("Здравствуйте, спасибо, что нашли время", 1 + 10))
pr.feed_me(her("А сколько это будет стоить для нашей компании", 2))
lines = wait(lambda e: e["type"] == "prompter" and e.get("kind") == "line" and "стоить" in e["line"]["text"])
got = [(e["line"]["who"], e["line"]["text"][:22]) for e in eng.events if e.get("kind") == "line"]
ok([w for w, _ in got] == ["them", "me", "them"], f"кто говорит — по голосу: {got}")
hint = wait(lambda e: e.get("kind") == "hint" and e["hint"].get("why") == "them" and e["hint"].get("done"))
ok(bool(hint), "на вопрос собеседницы — подсказка")
stuck = wait(lambda e: e.get("kind") == "hint" and e["hint"].get("stuck"), 6)
ok(bool(stuck) and stuck[0]["hint"]["id"] == hint[0]["hint"]["id"], "вы молчите после вопроса — подсказка выделяется («Начните так»)")

n0 = len(eng.events)
pr.feed_me(her("И какие у вас гарантии", 3))
wait(lambda e: e.get("kind") == "line" and "гарантии" in e["line"]["text"])
eng.mic_voice_t = time.monotonic() + 0.5                         # микрофон слышит речь — человек отвечает
pr.feed_me(me("Гарантия год, и поддержка круглосуточно", 3 + 10))
time.sleep(3)
ok(not [e for e in eng.events[n0:] if e.get("kind") == "hint" and e["hint"].get("stuck")], "заговорили сами — не подталкиваем")

pr.feed_me(me("Ну вот значит короче как бы", 4 + 10))
filler = wait(lambda e: e.get("kind") == "hint" and e["hint"].get("why") == "me" and e["hint"].get("done"))
ok(bool(filler) and "замешкался" not in filler[0]["hint"]["text"] and filler[0]["hint"]["q"], f"«ну, вот, значит, короче» — подсказка к последнему вопросу: {filler[:1] and filler[0]['hint']['q']}")

P.PAUSE_S, pr._t0 = 1.5, time.monotonic() - 120
for h in pr.hints:
    h["ts"] -= 60                                                 # прошлые подсказки — давно
eng.mic_voice_t, pr._them_voice_t = 0.0, 0.0
pause = wait(lambda e: e.get("kind") == "hint" and e["hint"].get("why") == "pause" and e["hint"].get("done"), 8)
ok(bool(pause) and "пилот" in pause[0]["hint"]["text"], "молчат оба — тихая подсказка, что сказать дальше")
time.sleep(2.5)
ok(len([e for e in eng.events if e.get("kind") == "hint" and e["hint"].get("why") == "pause" and e["hint"].get("done")]) == 1,
   "пауза — не чаще раза в полторы минуты")
pr.stop(summary=False)

# ---------- онлайн: чужой голос в микрофоне ----------
silent = tmp / "quiet.wav"
with wave.open(str(silent), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 30)
os.environ["DEWIAR_PROMPTER_WAV"] = str(silent)                  # «звук компьютера» — тишина
for them, want in ((True, [("me", "Сейчас расскажу про ")]), (False, [("them", "Сколько стоит подклю"), ("me", "Сейчас расскажу про ")])):
    eng.events.clear()
    pr.start(title="Онлайн", place="online", them=them)
    pr.feed_me(her("Сколько стоит подключение для отдела", 5))
    pr.feed_me(me("Сейчас расскажу про подключение", 5 + 10))
    wait(lambda e: e.get("kind") == "line" and "расскажу" in e["line"]["text"])
    time.sleep(0.5)
    got = [(e["line"]["who"], e["line"]["text"][:20]) for e in eng.events if e.get("kind") == "line"]
    ok(got == want, ("онлайн: собеседник из колонок в микрофоне не попадает в расшифровку дважды" if them
                     else "онлайн без звука компьютера: чужой голос в микрофоне — собеседник") + f": {got}")
    pr.stop(summary=False)

# ---------- забыть ----------
pr.enroll_forget()
ok(not pr.voice_info()["has"] and not S.OWNER_FILE.exists(), "голос можно забыть")
srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
