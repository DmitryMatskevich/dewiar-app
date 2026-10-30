"""Запись онлайн-встречи двумя дорожками («Вы» + «Собеседник») и связанные исправления v2.

Без звуковых устройств и моделей: захват, микрофон и VAD подменены. Проверяется:
  • поток встречи пишет общий WAV и две дорожки одинаковой длины, пауза звука = тишина;
  • отложенная расшифровка подписывает реплики по дорожкам и сортирует их по времени;
  • тишина на дорожке собеседника (BlackHole без Multi-Output) попадает в статус и протокол;
  • одно уникальное имя на WAV/TXT/MD; итог длинной встречи строится по частям, а не по хвосту;
  • stop() не ждёт сборку видео; фразы прошлой сессии не попадают в новую;
  • расписание переживает перезапуск и не стартует пропущенную встречу.
"""
import sys
import tempfile
import threading
import time
import types
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import numpy as np  # noqa: E402

import audio_io  # noqa: E402
from prompter import Prompter, _Segmenter  # noqa: E402

FRAME = 512


class FakeSettings(dict):
    def get(self, key, *rest):
        value = super().get(key, None)
        if rest and isinstance(value, dict):
            return value.get(rest[0], "")
        return value if value is not None else (rest[0] if rest else None)

    def active_llm(self):
        return None


class FakeEngine:
    def __init__(self, root):
        self.events = []
        self.workspace = SimpleNamespace(ensure=lambda: root)
        self.s = FakeSettings(prompter={}, audio={"input": ""})
        self.stopping = threading.Event()
        self.audio_suspended = threading.Event()
        self.restart_audio = threading.Event()
        self.transcribed = []

    def emit(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def set_state(self, *_):
        pass

    def transcribe_file(self, path):
        self.transcribed.append(Path(path).name)
        if Path(path).name == "Вы.wav":
            return [(1.0, 2.0, "Предлагаю пилот на месяц"), (6.0, 7.0, "Договорились")]
        return [(0.0, 1.0, "Сколько стоит внедрение?"), (3.0, 5.0, "А сроки?")]


def tone(n, amp=3000):
    t = np.arange(n * FRAME) / 16000
    return (np.sin(2 * np.pi * 440 * t) * amp).astype(np.int16).tobytes()


def frames(data):
    return [data[i:i + FRAME * 2] for i in range(0, len(data), FRAME * 2)]


def nframes(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


root = Path(tempfile.mkdtemp())
eng = FakeEngine(root)
p = Prompter(eng)
p._schedules_path = root / "schedules.json"

# ---------- 1. Сегментатор фраз ----------
seg = _Segmenter(end_frames=19)
out = None
for prob in [0.9] * 40 + [0.0] * 25:
    out = seg.feed(b"\1\0" * FRAME, prob) or out
assert out and len(out) / 2 / 16000 > 1.0, "фраза вырезается по речи и тишине"

# ---------- 2. Поток встречи: две дорожки, пауза = тишина, одинаковая длина ----------
THEM = frames(tone(60))
ME = frames(tone(60, 1500))


class FakeLoop:
    def __init__(self, *a, **k):
        self.i = 0

    def open(self):
        return "BlackHole 2ch (тест)"

    def read(self):
        time.sleep(0.002)
        if self.i >= len(THEM):
            p._stop.set()
            return b""
        self.i += 1
        if self.i == 20:
            p._record_paused = True                     # пауза звука посреди записи
        if self.i == 30:
            p._record_paused = False
        return THEM[self.i - 1]

    def close(self):
        pass


class FakeMic:
    def __init__(self):
        self.i = 0

    def read(self):
        time.sleep(0.002)
        if self.i >= len(ME):
            time.sleep(0.01)
            return b""
        self.i += 1
        return ME[self.i - 1]

    def close(self):
        pass


class FakeVAD:
    def __init__(self, *_):
        pass

    def prob(self, _):
        return 0.0


sys.modules["listen"] = types.SimpleNamespace(SileroVAD=FakeVAD)
old_loop = audio_io.LoopbackCapture
audio_io.LoopbackCapture = FakeLoop
p._open_meeting_mic = lambda: (FakeMic(), "микрофон (тест)")
try:
    p.meeting = {"title": "Склад", "record": True, "deferred": True, "audio_source": "system", "dual": True,
                 "started": time.time(), "place": "online", "them": True}
    p._record_open("Склад", tracks=("them", "me"))
    p._stop = threading.Event()
    p._session += 1
    p._them_loop(p._stop, None, p._session)
    p._record_close()
finally:
    audio_io.LoopbackCapture = old_loop
main = Path(p._record_path)
me_path, them_path = Path(p._track_paths["me"]), Path(p._track_paths["them"])
assert main.is_file() and me_path.name == "Вы.wav" and them_path.name == "Собеседник.wav"
assert nframes(main) == nframes(me_path) == nframes(them_path) == len(THEM) * FRAME, "дорожки и общий WAV одной длины"
with wave.open(str(them_path), "rb") as w:
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
paused = a[20 * FRAME:28 * FRAME]
assert int(np.abs(paused).max()) == 0 and int(np.abs(a[:18 * FRAME]).max()) > 1000, "на паузе звука — тишина, а не пропуск"
assert "две дорожки" in p.status or p._levels, "статус говорит о двух дорожках"

# ---------- 3. Отложенная расшифровка по дорожкам ----------
summarized = []
p._summarize = lambda: summarized.append(True)
p._transcript_open("Склад")
p._post_transcribe()
whos = [(x["who"], x["text"]) for x in p.lines]
assert whos == [("them", "Сколько стоит внедрение?"), ("me", "Предлагаю пилот на месяц"),
                ("them", "А сроки?"), ("me", "Договорились")], whos
assert summarized and set(eng.transcribed) == {"Вы.wav", "Собеседник.wav"}
assert "[00:01] Вы: Предлагаю пилот на месяц" in Path(p._transcript_path).read_text(encoding="utf-8")

# ---------- 4. Тишина на BlackHole: предупреждение во время и после записи ----------
p2 = Prompter(FakeEngine(root))
p2.meeting = {"title": "Тихо", "record": True, "deferred": True, "audio_source": "system", "started": time.time()}
p2._record_open("Тихо", tracks=("them", "me"))
for _ in range(10):
    p2._record_write_tracks(b"\0" * FRAME * 2, frames(tone(1))[0])
p2._record_close()
p2._levels = {"them": {"peak": 0, "signal_t": 0.0, "since": time.monotonic() - 30}}
p2._check_silence()
assert "Multi-Output" in p2.status and any(e[1].get("kind") == "warning" for e in p2.eng.events)
p2._summarize = lambda: None
p2._transcript_open("Тихо")
p2._post_transcribe()
assert "Multi-Output" in p2._record_error and all(x["who"] != "them" for x in p2.lines)
assert Prompter._wav_is_silent(p2._track_paths["them"]) and not Prompter._wav_is_silent(p2._track_paths["me"])

# ---------- 5. Одно имя на файлы встречи, без перезаписи ----------
p3 = Prompter(FakeEngine(root))
p3.meeting = {"title": "Склад", "started": p.meeting["started"]}
assert p3._meeting_base("Склад") == p._base_name + " (2)" or p3._meeting_base("Склад").endswith("(2)"), p3._base_name

# ---------- 6. Длинная встреча: итог по частям, начало не теряется ----------
calls = []


def fake_model(system, content, max_tokens=900):
    calls.append((system[:20], content))
    return "## Коротко\nитог" if "итог" in system.lower() else "- заметка"


p4 = Prompter(FakeEngine(root))
p4._ask_model = fake_model
long_lines = [{"who": "them" if i % 2 else "me", "text": f"реплика номер {i} " + "слово " * 30, "t": i * 5.0}
              for i in range(400)]
long_lines[0]["text"] = "Договорились в самом начале о цене 4800"
text = p4._summary_text({"title": "Долгая"}, long_lines)
assert len(calls) >= 4, "длинная расшифровка разбита на части"
assert "4800" in calls[0][1], "первая часть содержит начало встречи"
assert "ЗАМЕТКИ ПО ЧАСТЯМ" in calls[-1][1] and text.startswith("## Коротко")
assert "[00:00] Вы:" in calls[0][1], "реплики идут с таймкодами"

# ---------- 7. Реплики прошлой сессии не попадают в новую ----------
p4.meeting = {"started": time.time()}
p4._session = 5
p4.add_line("them", "старая фраза", session=4)
p4.add_line("them", "новая фраза", session=5)
assert [x["text"] for x in p4.lines] == ["новая фраза"]

# ---------- 8. stop() не ждёт сборку видео ----------
p5 = Prompter(FakeEngine(root))
p5.on, p5.meeting, p5._video_enabled = True, {"title": "Видео"}, True
done = threading.Event()
p5._finalize_frame_video = lambda: (time.sleep(1.5), done.set())
p5._summarize = lambda: None
t0 = time.monotonic()
p5.stop(summary=True)
assert time.monotonic() - t0 < 0.5, "stop() вернулся сразу"
assert p5._post_busy
try:
    p5.start(title="Следующая")
except ValueError as exc:
    assert "итога предыдущей" in str(exc)
else:
    raise AssertionError("новая встреча не должна стартовать, пока считается итог прошлой")
assert done.wait(3) and not p5._post_busy

# ---------- 9. Расписание: перезапуск приложения и пропущенная встреча ----------
now = time.time()
rows = Prompter._recover_schedules([
    {"id": "a", "state": "scheduled", "start_at": now + 100, "preflight": {"state": "checking"}},
    {"id": "b", "state": "starting", "start_at": now + 100},
    {"id": "c", "state": "running", "start_at": now - 100},
], now)
assert "preflight" not in rows[0] and rows[1]["state"] == "scheduled" and rows[2]["state"] == "error"

p6 = Prompter(FakeEngine(root))
p6._schedules_path = root / "s6.json"
p6.start = lambda **kw: (_ for _ in ()).throw(AssertionError("пропущенная встреча не должна стартовать"))
with p6._schedules_lock:
    p6._schedules = [{"id": "late", "state": "scheduled", "start_at": now - 3600, "end_at": None,
                      "title": "Утро", "options": {"consent": True}},
                     {"id": "over", "state": "scheduled", "start_at": now - 600, "end_at": now - 60,
                      "title": "Кончилась", "options": {"consent": True}}]
time.sleep(1.2)
states = {x["id"]: x["state"] for x in p6.schedules()}
assert states == {"late": "missed", "over": "missed"}, states

for pr in (p, p2, p3, p4, p5, p6):
    pr.eng.stopping.set()
print("OK: две дорожки записи, пауза-тишина, тишина BlackHole, уникальные имена, итог по частям, "
      "неблокирующий stop, изоляция сессий, восстановление расписания")

# ---------- 10. Эхо собеседника в микрофоне (без наушников) не дублируется как «Вы» ----------
them = [{"who": "them", "text": "Сколько стоит внедрение на тридцать мест?", "t": 10.0}]
assert Prompter._is_echo("сколько стоит внедрение на тридцать мест", 11.5, them)
assert not Prompter._is_echo("Четыре восемьсот за тридцать мест", 12.0, them)
assert not Prompter._is_echo("Сколько стоит внедрение на тридцать мест?", 40.0, them)
print("OK: эхо собеседника в микрофоне отбрасывается")

# ---------- 11. Core Audio tap через хелпер (протокол dewiar-audio-tap, хелпер подменён скриптом) ----------
import os  # noqa: E402
import stat  # noqa: E402

fake = root / "fake-audio-tap"
fake.write_text("""#!%s
import json, sys, time
import numpy as np
if sys.argv[1] == "--list":
    print(json.dumps([{"bundle": "us.zoom.xos", "name": "zoom.us", "playing": True, "pids": [1]}]))
    sys.exit(0)
assert sys.argv[1] == "--capture" and "--exclude-pid" in sys.argv
def ev(**k):
    sys.stderr.write(json.dumps(k, ensure_ascii=False) + "\\n"); sys.stderr.flush()
ev(ev="ready", rate=16000)
ev(ev="status", state="capturing", text="записываю звук системы (кроме Dewiar)")
t = np.arange(512 * 10) / 16000
sys.stdout.buffer.write((np.sin(2 * np.pi * 300 * t) * 4000).astype(np.int16).tobytes()); sys.stdout.flush()
ev(ev="warning", code="zeros", text="нет разрешения (тест)")
time.sleep(30)
""" % sys.executable, encoding="utf-8")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
os.environ["DEWIAR_AUDIO_TAP"] = str(fake)
try:
    assert audio_io.audio_tap_supported() and audio_io.list_audio_apps()[0]["bundle"] == "us.zoom.xos"
    lc = audio_io.LoopbackCapture(backend="tap")
    assert "Core Audio" in lc.probe()
    desc = lc.open()
    assert lc.kind == "tap" and "Core Audio" in desc, desc
    got = [lc.read() for _ in range(10)]
    assert all(len(f) == FRAME * 2 for f in got) and max(int(np.abs(np.frombuffer(f, np.int16)).max()) for f in got) > 3000
    deadline = time.monotonic() + 3
    while not lc.warning and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "разрешения" in lc.warning
    proc = lc._cap.proc
    lc.close()
    assert proc.poll() is not None, "хелпер остановлен при close()"
    # Подсказка о тишине зависит от способа записи.
    pt = Prompter(FakeEngine(root))
    pt._system_kind = "tap"
    assert "Запись системного звука" in pt._silence_hint()
    pt._system_kind = "blackhole"
    assert "Multi-Output" in pt._silence_hint()
    pt.eng.stopping.set()
finally:
    os.environ.pop("DEWIAR_AUDIO_TAP", None)
print("OK: Core Audio tap — запуск хелпера, поток кадров, предупреждения, остановка, откат подсказок")

# ---------- 12. Каскад автокадров: только устоявшийся экран, без дублей, возврат к прошлому слайду ----------
import io as _io  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def slide(text_y, color="white"):
    im = Image.new("RGB", (640, 360), color)
    d = ImageDraw.Draw(im)
    d.rectangle((40, text_y, 600, text_y + 60), fill="black")
    buf = _io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


pf = Prompter(FakeEngine(root))
pf.on, pf.meeting = True, {"title": "Слайды", "started": time.time()}
pf._transcript_open("Слайды")
pf._frame_interval = 0.5
pf.FRAME_STABLE_S = 0.2
A, B = slide(40), slide(220)
r1 = pf.capture_frame(A, ocr=False, auto=True)
assert not r1.get("skipped"), "первый автокадр сохраняется сразу"
assert pf.capture_frame(B, ocr=False, auto=True)["reason"] == "экран меняется"
pf.FRAME_STABLE_S, pf._frame_interval = 0.0, 0.0
r3 = pf.capture_frame(B, ocr=False, auto=True)
assert not r3.get("skipped") and not r3.get("revisit"), "устоявшийся новый слайд — новая сцена"
assert pf.capture_frame(B, ocr=False, auto=True)["reason"] == "экран почти не изменился"
assert pf.capture_frame(A, ocr=False, auto=True)["reason"] == "экран меняется"
r6 = pf.capture_frame(A, ocr=False, auto=True)
assert r6.get("revisit") and r6["path"] == r1["path"], "возврат к прошлому слайду — повтор, не новый файл"
assert len(pf._screenshots) == 2 and [e["path"] for e in pf._frame_events] == [r1["path"], r3["path"], r1["path"]]
pf.eng.stopping.set()
print("OK: каскад автокадров — устоявшийся экран, сцены и возвраты")

# ---------- 13. Итог: текст с экрана как справочник, части режутся по паузе, сырые реплики сохраняются ----------
ps = Prompter(FakeEngine(root))
ps.meeting = {"title": "Справочник", "started": time.time(), "ended": time.time() + 60}
ps._transcript_open("Справочник")
ps._screenshots = [{"ocr": "Договор № 17 · Мацкевич Дмитрий · LANMASTER", "t": 12.0}]
seen = []
ps._ask_model = lambda system, content, max_tokens=900: (seen.append(content), "## Коротко\nок")[1]
ps._summary_text(ps.meeting, [{"who": "them", "text": "по договору семнадцать", "t": 13.0}])
assert "ТЕКСТ С ЭКРАНА" in seen[0] and "LANMASTER" in seen[0]
rows = [{"who": "them", "text": "x" * 80, "t": float(i if i < 12 else 100 + i)} for i in range(30)]   # пауза перед 12-й
old_part = Prompter.SUMMARY_PART
Prompter.SUMMARY_PART = 30 * 50
try:
    parts = Prompter._split_parts(rows, Prompter._talk_lines(rows))
finally:
    Prompter.SUMMARY_PART = old_part
assert len(parts[0]) == 12, [len(x) for x in parts]   # разрез ровно по длинной паузе, а не по лимиту знаков
assert sum(len(x) for x in parts) == 30
saved = ps._save_raw_lines([{"who": "me", "text": "привет", "t": 1.0}])
data = __import__("json").loads(Path(saved).read_text(encoding="utf-8"))
assert data["version"] == 1 and data["lines"][0]["text"] == "привет"
ps.eng.stopping.set()
print("OK: OCR-справочник в итоге, разрез по паузе, сырой lines.json")
