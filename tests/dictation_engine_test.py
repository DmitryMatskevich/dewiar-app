"""Continuous chat dictation: VAD segments, explicit stop/send, ordered STT and cancelled sessions."""
import collections
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from engine_audio import AudioMixin, RATE, FRAME, FRAME_S


class Recorder(AudioMixin):
    def __init__(self, pause=5000):
        self._dictation_lock = threading.RLock()
        self._dictation_session = 0
        self._dictation_closed = self._dictation_submit = False
        self.dictation_active = self.dictation_finish_requested = False
        self.server = self.muted = self.stop_utt = False
        self.hold = None
        self.state = "idle"
        self.stt_q = queue.Queue()
        self.events = []
        self.finishing = threading.Event()
        self.stopping, self.restart_audio = threading.Event(), threading.Event()
        self.aliases = {"lira": ["лира"], "patrik": ["патрик"]}
        self.s = SimpleNamespace(get=lambda *k: {
            ("persona",): "lira", ("audio", "end_silence_ms"): pause,
            ("audio", "end_silence_auto"): False,
        }.get(k))
        self.status = {"aec": True}
        self.wav_input = "test"  # no device or microphone permissions
        self._mic_ring = collections.deque(maxlen=40)
        self._listen_peak = self._listen_vad = 0
        self._pending_media_stop = 0
        self.utt_id = 0
        self.meeting = self.alarm = None
        self.radio = SimpleNamespace(playing=False)
        self._level = lambda *a: None
        self.play_tone = lambda *a: None
        self.vosk_stt = None

    def set_state(self, state):
        self.state = state

    def emit(self, kind, **data):
        if kind == "dictation":
            self.events.append(data)
            if data.get("state") == "finishing":
                self.finishing.set()

    def interrupt(self, *args, **kwargs):
        pass

    def handle_text(self, *args, **kwargs):
        raise AssertionError("A draft must not be sent by a pause")

    def _dictation_done(self, sid):
        super()._dictation_done(sid)
        self.stopping.set()  # end test STT worker after the FIFO barrier

    def listen(self, speech, stop_at=None, wake_at=None, wake="лира"):
        """Run the real capture loop with synthetic VAD frames."""
        pos = [-1]

        def read():
            pos[0] += 1
            if pos[0] >= len(speech):
                return b""
            if pos[0] == stop_at:
                self.dictation_finish()
            return b"\x01\x00" * FRAME

        self.capture = SimpleNamespace(read=read)
        self.vad = SimpleNamespace(prob=lambda f: .9 if speech[pos[0]] else .01, reset=lambda: None)
        self.spotter = SimpleNamespace(
            feed=lambda f: [("wake", wake)] if pos[0] == wake_at else [],
            last_final=None, rec=SimpleNamespace(Reset=lambda: None),
        )
        self._listen()

    def transcribe(self, texts):
        values = iter(texts)
        self.current_stt = lambda: SimpleNamespace(name="test", transcribe=lambda pcm: next(values))
        self._stt_worker()


# Silence can last indefinitely; a pause segments audio and leaves capture active.
for pause in (5000, 10000):
    r = Recorder(pause)
    r.dictation_start()
    r.wake_time = -100
    quiet = int(pause / 1000 / FRAME_S) + 5
    r.listen([False] * quiet + [True] * 16 + [False] * quiet)
    assert r.dictation_active and r.state == "listening"
    assert r.stt_q.qsize() == 1
    assert not any(e["state"] == "done" for e in r.events)
    # More audio can be captured while the first fragment waits in STT.
    r.listen([True] * 16 + [False] * quiet)
    assert r.stt_q.qsize() == 2 and r.state == "listening"
    r.dictation_finish()
    r.listen([False] * 2)
    r.transcribe(["Первая часть.", "Вторая часть."])
    assert [e["text"] for e in r.events if e["state"] == "partial"] == ["Первая часть.", "Вторая часть."]
    assert r.events[-1] == {"state": "done", "submit": False, "session": r._dictation_session}

# Stop in the middle of speech flushes immediately, without waiting 10 seconds.
r = Recorder(10000)
r.dictation_start()
r.listen([True] * 30, stop_at=20)
assert r._dictation_closed and r.stt_q.qsize() == 2
r.transcribe(["Незаконченная фраза"])
assert r.events[-2]["text"] == "Незаконченная фраза" and not r.events[-1]["submit"]

# Stop when no speech ever occurred still completes, with no STT request.
r = Recorder()
r.dictation_start()
r.dictation_finish()
r.listen([False] * 2)
r.transcribe([])
assert r.events[-1] == {"state": "done", "submit": False, "session": r._dictation_session}

# A fast name detection must not submit a sentence before STT confirms its ending.
r = Recorder()
r.dictation_start()
r.listen([True] * 30, wake_at=20)
assert not r.dictation_finish_requested
r._dictation_result("Напиши письмо, Лира.", "test", r._dictation_session)
r.listen([False] * 2)
r.transcribe([])
assert [e["text"] for e in r.events if e["state"] == "partial"] == ["Напиши письмо"]
assert r.events[-1] == {"state": "done", "submit": True, "session": r._dictation_session}
assert sum(e["state"] == "done" for e in r.events) == 1

# A different agent's name does not close the active agent's dictation.
r = Recorder()
r.dictation_start()
r.listen([True] * 30, wake_at=20, wake="патрик")
assert r.dictation_active and not r.dictation_finish_requested

# Without a fast name detector, STT recognises the final name and requests submit.
r = Recorder()
r.dictation_start()
r._dictation_result("Составь список. Лира!", "test", r._dictation_session)
assert r.dictation_finish_requested and r._dictation_submit
assert r.events[-2]["text"] == "Составь список."
r.listen([False] * 2)
r.transcribe([])
assert r.events[-1]["submit"]

# Substrings are ordinary words; the stop word is also text in dictation mode.
r = Recorder()
r.dictation_start()
r._dictation_result("Лирами называют музыкальные инструменты. Стоп.", "test", r._dictation_session)
assert not r.dictation_finish_requested

# Exercise the real STT queue -> command detection -> capture flush -> FIFO done.
# Terminal words are stripped; a one-word utterance submits the preceding draft.
for texts, expected, persona in [
    (["Напиши письмо, ввод."], ["Напиши письмо"], "lira"),
    (["Напиши письмо, ВОТ!"], ["Напиши письмо"], "lira"),
    (["Первая часть.", "Ввод"], ["Первая часть."], "lira"),
    (["Первая часть.", "вот…"], ["Первая часть."], "lira"),
    (["ввод"], [], "lira"),
    (["Вот."], [], "lira"),
    (["Составь список, Патрик!"], ["Составь список"], "patrik"),
    (["Составь список. Лира!"], ["Составь список."], "lira"),
    (["Текст, ввод"], ["Текст"], "no-aliases"),
]:
    r = Recorder()
    r.s = SimpleNamespace(get=lambda *k, p=persona: p if k == ("persona",) else None)
    r.dictation_start()
    for _ in texts:
        r._on_utterance(b"\x01\x00" * RATE, 0, 1)
    worker = threading.Thread(target=r.transcribe, args=(texts,), daemon=True)
    worker.start()
    assert r.finishing.wait(2), texts
    r.listen([False] * 2)
    worker.join(2)
    assert not worker.is_alive(), texts
    assert [e["text"] for e in r.events if e["state"] == "partial"] == expected, (texts, r.events)
    assert r.events[-1] == {"state": "done", "submit": True, "session": r._dictation_session}, (texts, r.events)
    assert sum(e["state"] == "done" for e in r.events) == 1

for text in ["Вот план на завтра.", "Настрой ввод данных.", "Лира подготовит план.",
             "Расскажи про вводные.", "Поворот", "Вводить", "Составь список, Патрик."]:
    r = Recorder()
    r.dictation_start()
    r._dictation_result(text, "test", r._dictation_session)
    assert not r.dictation_finish_requested, text
    assert r.events[-1]["text"] == text

# Cancel during STT, then start again: an old result must not enter the new draft.
r = Recorder()
r.dictation_start()
sid = r._dictation_session
r._on_utterance(b"\x01\x00" * RATE, 0, 1)
cancelled = r.dictation_cancel()
assert cancelled["session"] == sid and r.events[-1]["session"] == sid
started = r.dictation_start()
assert started["session"] != sid and r.events[-1]["session"] == started["session"]
count = len(r.events)
r._dictation_result("Устаревший текст", "test", sid)
r._dictation_done(sid)
assert len(r.events) == count and r.dictation_active
r.stopping.clear()
r.dictation_finish(submit=True)
r.listen([False] * 2)
r.transcribe([])  # stale queued fragment is skipped before STT
assert r.events[-1]["submit"]
# STT can be busy while the capture thread adds more audio and the stop barrier.
r = Recorder()
r.dictation_start()
r._on_utterance(b"\x01\x00" * RATE, 0, 1)
entered, release = threading.Event(), threading.Event()
texts = iter(["Первая часть.", "Хвост."])
def delayed_stt(pcm):
    entered.set()
    assert release.wait(2), "test STT timed out"
    return next(texts)
r.current_stt = lambda: SimpleNamespace(name="slow-test", transcribe=delayed_stt)
worker = threading.Thread(target=r._stt_worker, daemon=True)
worker.start()
assert entered.wait(2)
assert r.state == "listening" and r.dictation_active
r.dictation_finish(submit=True)
r._dictation_flush(b"\x01\x00" * RATE)
assert not any(e["state"] == "done" for e in r.events)
release.set()
worker.join(2)
assert not worker.is_alive()
assert [e["text"] for e in r.events if e["state"] == "partial"] == ["Первая часть.", "Хвост."]
assert r.events[-1] == {"state": "done", "submit": True, "session": r._dictation_session}

# Ordinary voice questions must not be submitted just because 25 seconds elapsed.
r = Recorder()
r.wake_time = 0
r.follow_up_until = float('inf')
r.state = 'listening'
utterances = []
r._on_utterance = lambda pcm, t0, now: utterances.append(pcm)
r.listen([True] * int(70 / FRAME_S))
assert utterances == [], 'A long voice question was sent before the user paused'
r = Recorder()
r.wake_time = 0
r.follow_up_until = float('inf')
r.state = 'listening'
r._on_utterance = lambda pcm, t0, now: utterances.append(pcm)
r.listen([True] * int(70 / FRAME_S) + [False] * int(6 / FRAME_S))
assert len(utterances) == 1 and len(utterances[0]) / (RATE * 2) >= 69, 'Long voice question lost its beginning or end'

print("OK: continuous dictation, 5/10-second pauses, 70-second voice question, name/send, FIFO and stale session isolation")

print("OK")
