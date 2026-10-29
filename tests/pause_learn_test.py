"""Пауза конца фразы под манеру речи (engine_audio._pause_*): человек продолжил говорить меньше чем через 0,8 с после того,
как фразу сочли законченной, а модель ещё думает, — обе части склеиваются в один вопрос, пауза растёт на 150 мс (до +900);
ответы без обрыва понемногу возвращают её; выключено — не учится. Без звука и модели: ядро — заглушка."""
import os
import queue
import sys
import time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
import engine as E  # noqa: E402
from engine_base import FRAME_S, RATE  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


class S:
    def __init__(self, auto=True):
        self.d = {"audio": {"end_silence_ms": 650, "end_silence_auto": auto, "end_silence_extra": 0, "asr_wake": False, "barge_in": "smart"}}
        self.saves = 0

    def get(self, sec, key=None):
        v = self.d.get(sec, {})
        return v.get(key) if key else v

    def update(self, patch):
        self.saves += 1
        for k, v in patch.items():
            self.d.setdefault(k, {}).update(v)


class VP:
    items = []


def make(auto=True):
    e = E.Engine.__new__(E.Engine)
    e.s = S(auto)
    e.state = "idle"; e.utt_id = 0; e.stop_utt = False; e.calib = None; e.vosk_wake_check = -1; e.barge_sent = -1; e.barge_hit = -1
    e.barge_speech = -1; e.voiceprints = VP(); e.wake_time = 0.0; e.follow_up_until = 0.0; e.meeting = None; e.last_speak_end = 0.0
    e.status = {"aec": True}; e.stt_q = queue.Queue(); e.check_q = queue.Queue(); e.events = []; e.cancels = 0
    e.emit = lambda ev, **k: e.events.append((ev, k))
    e.set_state = lambda st: setattr(e, "state", st)
    e.cancel_turn = lambda: setattr(e, "cancels", e.cancels + 1)
    e._pause_apply()
    return e


def pcm(sec):
    return b"\x01\x00" * int(RATE * sec)


# 1. обрыв: «Лира, напомни мне завтра…» [пауза 0,9 с] «…позвонить маме»
e = make()
f0 = e._end_frames
now = time.monotonic()
e.utt_id = 1; e.wake_time = now - 1.0                       # имя прозвучало в этой фразе
e._on_utterance(pcm(1.6), now - 1.6, now)
first = e.stt_q.get_nowait()
ok(first[1] is True and e.state == "thinking", "первая часть — в распознавание, модель думает")
e.utt_id = 2
e._pause_check(now + 0.3)
ok(e.s.d["audio"]["end_silence_extra"] == 150 and e._end_frames > f0, f"продолжил через 0,3 с — пауза длиннее: {e.s.d['audio']['end_silence_extra']} мс, кадров {f0} → {e._end_frames}")
ok(any(ev == "pause_learned" for ev, _ in e.events), "студии сообщено")
e._on_utterance(pcm(1.2), now + 0.3, now + 1.5)
merged = e.stt_q.get_nowait()
ok(len(merged[0]) / 2 / RATE > 3.0 and merged[1] is True and e.cancels == 1, f"обе части — один вопрос ({len(merged[0]) / 2 / RATE:.1f} с), первая отменена")

# 2. не обрыв: продолжение через 2 с или когда уже отвечает
e = make()
now = time.monotonic()
e.utt_id = 1; e.wake_time = now - 1.0
e._on_utterance(pcm(1.5), now - 1.5, now)
e.stt_q.get_nowait()
e.utt_id = 2; e._pause_check(now + 2.0)
ok(e.s.d["audio"]["end_silence_extra"] == 0, "заговорил через 2 с — это новая фраза, пауза та же")
e = make(); now = time.monotonic(); e.utt_id = 1; e.wake_time = now - 1.0
e._on_utterance(pcm(1.5), now - 1.5, now); e.stt_q.get_nowait()
e.state = "speaking"; e.utt_id = 2; e._pause_check(now + 0.3)
ok(e.s.d["audio"]["end_silence_extra"] == 0, "ассистент уже отвечает — не учимся (может быть эхо)")

# 3. потолок и возврат
e = make()
for i in range(10):
    now = time.monotonic(); e.state = "idle"; e.utt_id += 1; e.wake_time = now - 1.0
    e._on_utterance(pcm(1.0), now - 1.0, now); e.stt_q.get_nowait()
    e.utt_id += 1; e._pause_check(now + 0.2)
ok(e.s.d["audio"]["end_silence_extra"] == 600, f"не больше +600 мс (№226): {e.s.d['audio']['end_silence_extra']}")
saves0 = e.s.saves
for i in range(20):
    now = time.monotonic(); e.state = "idle"; e.utt_id += 1; e.wake_time = now - 1.0
    e._on_utterance(pcm(1.0), now - 1.0, now); e.stt_q.get_nowait()
    e._pause_ok()
ok(e.s.d["audio"]["end_silence_extra"] == 240 and e.s.saves - saves0 == 6, f"20 ответов без обрыва — −360 мс (тает вдвое быстрее, №226), в настройки 6 раз: {e.s.d['audio']['end_silence_extra']}, {e.s.saves - saves0}")
ok(e._end_frames == max(8, int((650 + 240) / 1000 / FRAME_S)), "пауза применяется сразу")
e.s.d["audio"]["end_silence_extra"] = 900; e._pause_apply()
ok(e._end_frames == max(8, int((650 + 600) / 1000 / FRAME_S)), "выученные раньше +900 — ждём не дольше +600")

# 4. выключено
e = make(auto=False)
now = time.monotonic(); e.utt_id = 1; e.wake_time = now - 1.0
e._on_utterance(pcm(1.5), now - 1.5, now); e.stt_q.get_nowait()
e.utt_id = 2; e._pause_check(now + 0.3)
ok(e.s.d["audio"]["end_silence_extra"] == 0, "подстройка выключена — пауза не меняется (а склеить — склеивает)")
e._on_utterance(pcm(1.0), now + 0.3, now + 1.3)
ok(len(e.stt_q.get_nowait()[0]) / 2 / RATE > 2.5, "склеено и без подстройки")

print("\nИТОГ:", "всё хорошо" if not fails else f"ошибок {len(fails)}")
sys.exit(1 if fails else 0)
