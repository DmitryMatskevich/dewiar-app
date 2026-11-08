"""Разбиение встречи по участникам (core/diarization) — без моделей, сети и звуковых устройств.

Проверяется:
  • слова из токенов onnx-asr: знак препинания, выданный перед следующей речью, не продлевает слово;
  • назначение слов говорящим, сглаживание, реплики; кусок без слов — целиком;
  • порядок «Участник N», слияние раздробленных кластеров, кластер владельца;
  • правки отдельно от машинного результата и их перенос при пересчёте (по времени, без векторов голоса);
  • RTTM и .words.json, аудио участников (длина, тишина, края, повторный экспорт), подписи;
  • загрузка модели сегментации: подмена загрузчика, проверка SHA256, имена внутри архива;
  • отдельный процесс: прогресс, результат, ошибка, таймаут, отмена (подставной worker);
  • _post_transcribe: режим «По участникам» на двух дорожках и на одной, сбой процесса, «Без разметки»,
    быстрый путь (процесс не запускается), .lines.json версии 2, подписи в .txt;
  • очная встреча с записью пишет WAV микрофона и отдаёт фразы на «чья?»;
  • проигрыватель и правка прошлой встречи (service.edit / export_audio / speakers_view).
"""
import io
import json
import os
import sys
import tarfile
import tempfile
import threading
import time
import types
import wave
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(tempfile.mkdtemp(prefix="dewiar-diar-test-"))
os.environ["DEWIAR_STATE_DIR"] = str(STATE)            # модели и профиль голоса — только во временной папке
sys.path.insert(0, str(ROOT / "core"))

import numpy as np  # noqa: E402

from diarization import edits, identify, models, normal_mode, pipeline, runner, service, storage, tracks  # noqa: E402
from diarization.align import assign_speakers, group, segment_speaker, smooth  # noqa: E402
from diarization.labels import label  # noqa: E402
from diarization.schema import Turn, Word  # noqa: E402
from stt import tokens_to_words  # noqa: E402

N = 0


def ok(cond, what):
    global N
    if not cond:
        raise AssertionError(what)
    N += 1
    print("✅", what)


def write_wav(path, seconds, amp=3000, freq=440, silent=()):
    sr = 16000
    t = np.arange(int(seconds * sr)) / sr
    a = np.sin(2 * np.pi * freq * t) * amp
    for s, e in silent:
        a[int(s * sr):int(e * sr)] = 0
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a.astype(np.int16).tobytes())


# ---------- 1. Слова из токенов ----------
ws = tokens_to_words(["▁При", "вет", ",", " как", " ", "де", "ла", "?", " Ага", "."],
                     [0.0, 0.08, 0.16, 0.6, 0.9, 0.92, 1.0, 13.0, 13.2, 13.5], offset=10)
ok([w["t"] for w in ws] == ["Привет,", "как", "дела?", "Ага."], f"слова собраны из токенов: {ws}")
ok(ws[2]["e"] < 12.0, f"«?» перед следующей речью не продлевает слово: {ws[2]}")
ok(ws[0]["s"] == 10.0 and all(w["e"] >= w["s"] for w in ws), "время от начала куска, конец не раньше начала")
ok(tokens_to_words(None, None) == [], "нет токенов — нет слов")

# ---------- 2. Слова ↔ говорящие ----------
turns = [Turn(0, 5, "S0"), Turn(5.2, 9, "S1"), Turn(8.5, 8.9, "S0"), Turn(9.5, 20, "S0")]
words = [Word(f"w{i}", s, s + 0.3) for i, s in enumerate([0.5, 1, 5.3, 5.8, 7, 9.6, 10, 30])]
words.insert(3, Word("x", 5.35, 5.5))
words.sort(key=lambda w: w.start)
assign_speakers(words, turns)
ok(words[-1].speaker == "S0", "слово вне интервалов — говорящий предыдущего")
smooth(words)
utts = group(words)
ok([(u.speaker, u.text) for u in utts] == [("S0", "w0 w1"), ("S1", "w2 x w3 w4"), ("S0", "w5 w6"), ("S0", "w7")],
   f"реплики по смене говорящего и паузе: {[(u.speaker, u.text) for u in utts]}")
isl = [Word("a", 0, 1, "A"), Word("b", 1, 1.1, "B"), Word("c", 1.1, 2, "A")]
smooth(isl)
ok([w.speaker for w in isl] == ["A", "A", "A"], "короткий «остров» без наложения сглажен")
ok(segment_speaker(0, 10, [Turn(0, 6, "A"), Turn(6, 10, "B")]) == ("A", True), "кусок без слов — основному, с флагом")

# ---------- 3. Кто есть кто ----------
r = identify.renumber([Turn(0, 1, "S7"), Turn(1, 2, "S0"), Turn(2, 3, "S7")])
ok([t.speaker for t in r] == ["R1", "R2", "R1"], "метки движка → R1, R2 по порядку появления")
rng = np.random.default_rng(1)
a, b = rng.normal(size=192), rng.normal(size=192)
ok(identify.merge_close_clusters({"R1": a, "R2": a + 0.05 * rng.normal(size=192), "R3": b}) ==
   {"R1": "R1", "R2": "R1", "R3": "R3"}, "раздробленный голос сливается")
mine, best = identify.owner_clusters({"R1": a, "R2": b}, a, 0.55)
ok(mine == ["R1"] and best > 0.99, "кластер владельца по профилю")
c3 = rng.normal(size=192)
near = 0.7 * a / np.linalg.norm(a) + 0.71 * c3 / np.linalg.norm(c3)   # похожий чужой голос: косинус ≈ 0,7
mine, _ = identify.owner_clusters({"R1": a, "R2": near, "R3": a + 0.01 * rng.normal(size=192)}, a, 0.55)
ok(mine == ["R1", "R3"] or mine == ["R3", "R1"], f"похожий чужой голос не становится «Вы», раздробленный ваш — да: {mine}")
ok(identify.owner_clusters({"R1": b}, a, 0.55)[0] == [], "нет похожего — «Вы» не назначается")
# предел голосов: маленький отколовшийся кусок вливается в ближайший, большие кластеры не склеиваются
vv = {"R1": a, "R2": b, "R3": a + 0.05 * rng.normal(size=192), "R4": c3}
tl = [Turn(0, 30, "R1"), Turn(30, 60, "R2"), Turn(60, 62, "R3"), Turn(62, 90, "R4")]
lim = pipeline.limit_speakers(tl, lambda s_, e_: vv[next(t.speaker for t in tl if t.start == s_)], 3)
ok([t.speaker for t in lim] == ["R1", "R2", "R1", "R3"], f"предел 3: кусок R3 → R1, остальные целы: {[t.speaker for t in lim]}")
tl2 = [Turn(0, 30, "R1"), Turn(30, 60, "R2"), Turn(60, 61.5, "R3")]
vv2 = {"R1": b, "R2": c3, "R3": a + 0.05 * rng.normal(size=192)}
lim2 = pipeline.limit_speakers(tl2, lambda s_, e_: vv2[next(t.speaker for t in tl2 if t.start == s_)], 2, anchors={"ME": a})
ok([t.speaker for t in lim2] == ["R1", "R2", "ME"], f"кусок вашей речи при пределе уходит к «Вы», а не к чужому: {[t.speaker for t in lim2]}")
ok(pipeline.limit_speakers(tl, None, 3) == tl and pipeline.limit_speakers(tl, lambda *_: None, 0) == tl, "без векторов/предела — без изменений")
vec = {"R1": a, "R2": b}
tt = [Turn(0, 2, "R1"), Turn(2, 4, "R2")]
c = identify.centroids(tt, lambda s, e: vec["R1"] if s < 2 else vec["R2"])
ok(set(c) == {"R1", "R2"}, "центроиды по длинным интервалам")

# ---------- 4. Сборка: две дорожки и одна ----------
me_segs = [(0.0, 22.0, "Добрый день. Согласен.", [{"t": "Добрый", "s": 0.5, "e": 0.9}, {"t": "день.", "s": 0.9, "e": 1.4},
                                                    {"t": "Согласен.", "s": 15.0, "e": 15.6}])]
them_segs = [(0.0, 22.0, "Здравствуйте. Да.", [{"t": "Здравствуйте.", "s": 2.0, "e": 3.0}, {"t": "Да.", "s": 8.0, "e": 8.3}]),
             (22.0, 30.0, "Без слов", None)]
lines, sp, fin = pipeline.build_dual(me_segs, them_segs, [Turn(1.9, 3.1, "S3"), Turn(7.9, 8.5, "S0"), Turn(22.5, 29, "S3")])
ok([(x["who"], x["label"], x["text"]) for x in lines] ==
   [("me", "Вы", "Добрый день."), ("them", "Участник 1", "Здравствуйте."), ("them", "Участник 2", "Да."),
    ("me", "Вы", "Согласен."), ("them", "Участник 1", "Без слов")], f"две дорожки: {lines}")
ok([s["id"] for s in sp] == ["ME", "R1", "R2"], "участники: Вы, R1, R2")
one, _sp, _ = pipeline.build_dual([], them_segs[:1], [Turn(1.9, 9, "S5")])
ok(one[0]["label"] == "Собеседник", "один голос — «Собеседник»")
mix = [(0.0, 20.0, "раз два три", [{"t": "раз", "s": 1, "e": 1.5}, {"t": "два", "s": 6, "e": 6.5}, {"t": "три", "s": 11, "e": 11.5}])]
owner = a / np.linalg.norm(a)
emb = {"S0": a, "S1": b, "S2": a + 0.02 * rng.normal(size=192)}
spans = [Turn(0, 3, "S0"), Turn(5, 8, "S1"), Turn(10, 13, "S2")]
lookup = lambda s, e: next(emb[t.speaker] for t in spans if abs(t.start - s) < 0.01)  # noqa: E731
lines, sp, _ = pipeline.build_single(mix, spans, embed=lambda s, e: lookup(s, e) if s in (0, 5, 10) else None,
                                     owner=owner, owner_threshold=0.55)
ok([(x["who"], x["label"]) for x in lines] == [("me", "Вы"), ("them", "Собеседник"), ("me", "Вы")],
   f"одна дорожка: «Вы» по профилю, раздробленный голос владельца объединён: {lines}")
ok(pipeline.expected_speakers({"participants": "Вы, Анна, Пётр"}, {}) == (0, 2), "предел — по списку участников")
ok(service.config({})["threshold"] == 0.4, "порог кластеризации по умолчанию — 0,4 (подобран на стенде)")
ok(pipeline.expected_speakers({"expected_speakers": 3}, {}, single_track=True) == (4, 0), "очная: +1 за ваш голос")
ok(normal_mode("PARTICIPANTS") == "participants" and normal_mode("x") == "owner", "режимы")

# ---------- 5. Правки и перенос ----------
ed = json.loads(json.dumps(edits.EMPTY))
edits.rename(ed, "R1", "  Иван\x00 Петров ")
edits.merge(ed, "R3", "R1")
edits.reassign(ed, 8.0, 8.3, "ME")
base = [{"who": "them", "speaker": "R1", "label": "Участник 1", "t": 2.0, "end": 3.0, "text": "a"},
        {"who": "them", "speaker": "R2", "label": "Участник 2", "t": 8.0, "end": 8.3, "text": "b"},
        {"who": "them", "speaker": "R3", "label": "Участник 3", "t": 12.0, "end": 13.0, "text": "c"}]
speakers = [{"id": "ME", "label": "Вы"}, {"id": "R1", "label": "Участник 1"}, {"id": "R2", "label": "Участник 2"},
            {"id": "R3", "label": "Участник 3"}]
out, outsp = edits.apply(base, speakers, ed)
ok([(x["label"], x["who"]) for x in out] == [("Иван Петров", "them"), ("Вы", "me"), ("Иван Петров", "them")],
   f"имя, объединение, переназначение: {out}")
ok(base[0]["label"] == "Участник 1" and [s["id"] for s in outsp] == ["ME", "R1", "R2"], "исходные списки не меняются")
old = [Turn(0, 10, "R1"), Turn(10, 20, "R2"), Turn(30, 31, "R3")]
new = [Turn(0, 9, "R2"), Turn(9.5, 21, "R1")]
moved = edits.transfer(ed, old, new)
ok(moved["names"] == {"R2": "Иван Петров"}, f"имя перенесено по времени речи: {moved}")
ok(any(x["kind"] == "reassign" or x["kind"] == "name" for x in moved["not_transferred"]) or moved["reassign"],
   "непереносимое — в not_transferred")
ok(edits.map_speakers(old, new) == {"R1": "R2", "R2": "R1"}, "сопоставление старых и новых id")

# ---------- 6. Файлы: RTTM, слова, подписи ----------
tmp = Path(tempfile.mkdtemp())
storage.write_rttm(tmp / "a.rttm", [Turn(1.0, 2.5, "R1"), Turn(0.0, 1.0, "ME")], uri="Встреча 1")
ok(storage.read_rttm(tmp / "a.rttm") == [Turn(0.0, 1.0, "ME"), Turn(1.0, 2.5, "R1")], "RTTM туда и обратно")
storage.write_words(tmp / "w.json", {"me": me_segs})
back = storage.read_words(tmp / "w.json")
ok(back["me"][0][2] == me_segs[0][2] and storage.has_words(back), ".words.json туда и обратно")
ok(label({"who": "them", "label": "Иван"}) == "Иван" and label({"who": "me"}) == "Вы" and label({"who": "screen", "label": "x"}) == "Экран",
   "единая подпись")

# ---------- 7. Аудио участников ----------
src = tmp / "src.wav"
write_wav(src, 10)
info = tracks.export_speaker_track(src, [Turn(1, 2, "R1"), Turn(1.9, 3, "R1"), Turn(7, 8, "R1")], tmp / "t.wav")
with wave.open(str(tmp / "t.wav")) as w:
    out = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
ok(len(out) == 160000 and out[8000] == 0 and out[5 * 16000] == 0 and np.abs(out[24000:24100]).max() > 0,
   "timeline: та же длина, вне реплик тишина")
ok(abs(int(out[int(0.85 * 16000)])) < 50, "плавный край в начале реплики")
info = tracks.export_speaker_track(src, [Turn(1, 2, "R1"), Turn(7, 8, "R1")], tmp / "s.wav", mode="speech_only")
with wave.open(str(tmp / "s.wav")) as w:
    ok(abs(w.getnframes() / 16000 - 2.9) < 0.01 and len(info["timemap"]) == 2, "speech_only: только речь + карта времени")

# ---------- 8. Модель сегментации: загрузка без токенов, SHA256, имена в архиве ----------
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
    for name, data in ((f"{models.SEG_NAME}/model.onnx", b"fake-onnx"), ("../evil.onnx", b"x"),
                       (f"{models.SEG_NAME}/run.sh", b"#!/bin/sh")):
        ti = tarfile.TarInfo(name)
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
calls = []


def fake_download(url, dst, sha, on_progress=None):
    calls.append(url)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(buf.getvalue())
    return dst


import hashlib  # noqa: E402

real_sha = models.SEG_SHA256
try:
    models.SEG_SHA256 = "0" * 64
    try:
        models.ensure(download=fake_download, speaker=SimpleNamespace(ensure=lambda *_: None))
        raise AssertionError("неверная SHA256 принята")
    except RuntimeError as exc:
        ok("не скачалась" in str(exc) and not models.seg_model().exists(), "SHA256 не совпала — модели нет")
    models.SEG_SHA256 = hashlib.sha256(b"fake-onnx").hexdigest()
    calls.clear()
    models.ensure(mirror={"url": "https://mirror.example/seg.tar.bz2"}, download=fake_download,
                  speaker=SimpleNamespace(ensure=lambda *_: None))
    ok(calls == ["https://mirror.example/seg.tar.bz2"], "сначала своё зеркало")
    ok(models.seg_model().read_bytes() == b"fake-onnx" and not (models.seg_dir() / "run.sh").exists()
       and not (models.seg_dir().parent / "evil.onnx").exists(), "из архива — только модель, без путей наружу")
    ok("github.com/k2-fsa/sherpa-onnx/releases" in models.SEG_URL and "huggingface" not in models.SEG_URL,
       "исходный адрес — релизы sherpa-onnx на GitHub, без токена")
finally:
    models.SEG_SHA256 = real_sha
emb_path = models.embedding_model()
emb_path.parent.mkdir(parents=True, exist_ok=True)
emb_path.write_bytes(b"fake")                            # модель голоса «есть»: процесс подставной
ok(models.ready(), "модели на месте")

# ---------- 9. Отдельный процесс (подставной worker) ----------
fake = tmp / "fake_worker.py"
fake.write_text('''import json, os, sys, time
task = json.loads(sys.stdin.read())
mode = os.environ.get("FAKE_DIAR", "ok")
print("посторонний вывод", flush=True)
if mode == "sleep":
    time.sleep(30)
if mode == "error":
    print(json.dumps({"type": "error", "message": "RuntimeError: сломалось"}), flush=True); sys.exit(1)
for p in (0.5, 1.0):
    print(json.dumps({"type": "progress", "value": p}), flush=True)
turns = json.loads(open(os.environ["FAKE_TURNS"]).read())
print(json.dumps({"type": "result", "turns": turns, "speakers": len({t["speaker"] for t in turns}),
                  "engine": {"name": "sherpa-onnx", "version": "test", "num_clusters": task["num_speakers"]},
                  "elapsed": 0.1}), flush=True)
''', encoding="utf-8")
turns_file = tmp / "turns.json"
os.environ["FAKE_TURNS"] = str(turns_file)
os.environ["DEWIAR_DIARIZE_WORKER"] = sys.executable + "\x1f-B\x1f" + str(fake)
turns_file.write_text(json.dumps([{"start": 0.5, "end": 3.0, "speaker": "S1"}, {"start": 5.0, "end": 9.0, "speaker": "S0"}]))
prog = []
j = runner.start(src, "seg", "emb", num_speakers=2, on_progress=prog.append)
res = j.wait(20)
ok(res and res["engine"]["num_clusters"] == 2 and prog == [0.5, 1.0], f"прогресс и результат: {res}, {prog}")
ok([t.speaker for t in runner.turns_from_dicts(res["turns"])] == ["S1", "S0"], "интервалы из результата")
os.environ["FAKE_DIAR"] = "error"
j = runner.start(src, "seg", "emb")
ok(j.wait(20) is None and "сломалось" in j.error, "ошибка процесса — причина")
os.environ["FAKE_DIAR"] = "sleep"
j = runner.start(src, "seg", "emb")
t0 = time.monotonic()
ok(j.wait(0.5) is None and "время" in j.error and time.monotonic() - t0 < 10, "таймаут завершает процесс")
os.environ["FAKE_DIAR"] = "ok"

# ---------- 10. Отложенная расшифровка в prompter ----------
from prompter import Prompter  # noqa: E402


class FakeSettings(dict):
    def get(self, key, *rest):
        value = super().get(key, None)
        if rest and isinstance(value, dict):
            return value.get(rest[0], "")
        return value if value is not None else (rest[0] if rest else None)

    def active_llm(self):
        return None


WORDS = {
    "Вы.wav": [(0.0, 22.0, "Добрый день. Договорились.",
                [{"t": "Добрый", "s": 1.0, "e": 1.4}, {"t": "день.", "s": 1.4, "e": 1.9}, {"t": "Договорились.", "s": 15.0, "e": 15.8}])],
    "Собеседник.wav": [(0.0, 22.0, "Сколько стоит? А сроки?",
                        [{"t": "Сколько", "s": 3.0, "e": 3.4}, {"t": "стоит?", "s": 3.4, "e": 3.9},
                         {"t": "А", "s": 6.0, "e": 6.2}, {"t": "сроки?", "s": 6.2, "e": 6.8}])],
}


class FakeEngine:
    def __init__(self, root, cfg=None, words=True):
        self.events = []
        self.workspace = SimpleNamespace(ensure=lambda: root)
        self.s = FakeSettings(prompter=cfg or {}, audio={"input": ""})
        self.stopping = threading.Event()
        self.audio_suspended = threading.Event()
        self.restart_audio = threading.Event()
        self.transcribed = []
        self.words = words

    def emit(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def set_state(self, *_):
        pass

    def current_stt(self):
        return SimpleNamespace(transcribe=lambda pcm: "фраза", name="тест", ready=True)

    def transcribe_file(self, path, stt=None, words=False):
        self.transcribed.append((Path(path).name, words))
        rows = WORDS.get(Path(path).name) or [(0.0, 22.0, "Добрый день. Сколько стоит? А сроки?",
                                               [{"t": "Добрый", "s": 1.0, "e": 1.4}, {"t": "день.", "s": 1.4, "e": 1.9},
                                                {"t": "Сколько", "s": 3.0, "e": 3.4}, {"t": "стоит?", "s": 3.4, "e": 3.9},
                                                {"t": "А", "s": 6.0, "e": 6.2}, {"t": "сроки?", "s": 6.2, "e": 6.8}])]
        if words and self.words:
            return rows
        return [r[:3] for r in rows]


class FakeSpeaker:
    """Профиль владельца без модели: голос считаем по уровню сигнала отрезка (тест различает «громкий» — вы)."""
    mirror = None
    owner_n = 4

    def __init__(self):
        self.owner = np.array([1.0, 0.0], np.float32)

    def embed(self, pcm):
        a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if len(a) < 8000:
            return None
        loud = float(np.abs(a).mean()) > 3000
        return np.array([1.0, 0.0] if loud else [0.0, 1.0], np.float32)

    def diarizer(self):
        sp = self

        class D:
            def who(self, pcm):
                v = sp.embed(pcm)
                return ("me" if v is not None and v[0] > 0.5 else "them"), 0.0
        return D()

    def ready(self):
        return True


def meeting(p, title, dual, place="online", mode="participants", participants="", expected=0):
    p.meeting = {"title": title, "record": True, "deferred": True, "audio_source": "system", "dual": dual,
                 "started": time.time(), "place": place, "them": place != "live", "speaker_mode": mode,
                 "expected_speakers": expected, "participants": participants}
    p._record_open(title, tracks=("them", "me") if dual else ())
    p._record_close()
    p._summarize = lambda: p._save_raw_lines(list(p.lines))      # итог не нужен, сырые реплики — как в _summarize
    p._transcript_open(title)


root = Path(tempfile.mkdtemp())
eng = FakeEngine(root)
p = Prompter(eng)
p._speaker = FakeSpeaker()
meeting(p, "Склад", dual=True, expected=2)
for name in ("Вы", "Собеседник"):
    write_wav(Path(p._track_paths["me" if name == "Вы" else "them"]), 25)
write_wav(Path(p._record_path), 25)
turns_file.write_text(json.dumps([{"start": 2.9, "end": 4.0, "speaker": "S4"}, {"start": 5.9, "end": 7.0, "speaker": "S0"}]))
p._post_transcribe()
got = [(x["who"], x.get("label"), x["text"]) for x in p.lines]
ok(got == [("me", "Вы", "Добрый день."), ("them", "Участник 1", "Сколько стоит?"), ("them", "Участник 2", "А сроки?"),
           ("me", "Вы", "Договорились.")], f"две дорожки по участникам: {got}")
ok(all(w for _n, w in eng.transcribed), "распознавание просит время слов")
txt = Path(p._transcript_path).read_text(encoding="utf-8")
ok("[00:03] Участник 1: Сколько стоит?" in txt and "«Участник N»" in txt, "в .txt — подписи участников и пояснение")
raw = json.loads(Path(p._transcript_path).with_suffix(".lines.json").read_text(encoding="utf-8"))
ok(raw["version"] == 2 and [s["id"] for s in raw["speakers"]] == ["ME", "R1", "R2"]
   and raw["diarization"]["status"] == "ok" and raw["lines"][1]["speaker0"] == "R1", ".lines.json версии 2 с машинным результатом")
ok(storage.words_path(p._transcript_path).is_file() and storage.rttm_path(p._transcript_path).is_file(),
   "рядом — .words.json и .diarization.rttm")

# правка прошлой встречи и проигрыватель
rel = f"Встречи/{Path(p._record_path).stem}.md"
mroot = root / "Встречи"
(mroot / f"{Path(p._record_path).stem}.md").write_text("# итог", encoding="utf-8")
view = service.speakers_view(mroot, rel)
ok(view["can_diarize"] and [s["id"] for s in view["speakers"]] == ["ME", "R1", "R2"], "состояние участников встречи")
service.edit(mroot, rel, "rename", id="R1", name="Иван")
service.edit(mroot, rel, "merge", id="R2", to="R1")
txt = Path(p._transcript_path).read_text(encoding="utf-8")
ok("Иван: Сколько стоит?" in txt and "Иван: А сроки?" in txt, "переименование и объединение — в .txt")
ed = edits.load(edits.path_for(p._transcript_path))
raw = json.loads(Path(p._transcript_path).with_suffix(".lines.json").read_text(encoding="utf-8"))
ok(ed["names"] == {"R1": "Иван"} and raw["lines"][2]["speaker0"] == "R2", "правки отдельно, машинный результат цел")
service.edit(mroot, rel, "rename", id="R1", name="Иван Петров")
txt = Path(p._transcript_path).read_text(encoding="utf-8")
ok("Иван Петров: А сроки?" in txt, "повторная правка поверх объединения")
outa = service.export_audio(mroot, rel)
names = sorted(x["file"] for x in outa["files"])
ok(names == ["Вы.wav", "Иван Петров.wav"], f"аудио участников: {names}")
from meeting_media import player_data  # noqa: E402

media = SimpleNamespace(url=lambda rel_: "/m/" + rel_)
pd = player_data(mroot, rel, media)
ok(any(t.get("participant") for t in pd["tracks"]) and any(x.get("label") == "Иван Петров" for x in pd["lines"])
   and pd["speakers"], "проигрыватель: дорожки и подписи участников")

# пересчёт прошлой встречи: имена переносятся по времени речи
turns_file.write_text(json.dumps([{"start": 2.8, "end": 4.1, "speaker": "S9"}, {"start": 5.8, "end": 7.1, "speaker": "S2"}]))
out = service.rebuild(mroot, rel, {}, speaker=p._speaker)
txt = Path(p._transcript_path).read_text(encoding="utf-8")
ok("Иван Петров: Сколько стоит?" in txt and out["words"], f"пересчёт без распознавания, правки перенесены: {out}")

# команды ядра для проигрывателя (engine_commands._meeting_speakers_cmd)
from engine_commands import CommandsMixin  # noqa: E402

cmd_self = SimpleNamespace(workspace=SimpleNamespace(ensure=lambda: root), s=FakeSettings(prompter={}),
                           prompter=p, transcribe_file=lambda *a_, **k_: [], _mirror=lambda key: None)
cmd = lambda command, **kw: CommandsMixin._meeting_speakers_cmd(cmd_self, command, dict(kw, path=rel))  # noqa: E731
v = cmd("prompter_meeting_speakers")
ok(v["job"] is None and v["speakers"][1]["display"] == "Иван Петров", "команда: состояние участников")
r = cmd("prompter_meeting_speakers_diarize", expected_speakers=2)
ok(r["started"], "команда: пересчёт в фоне")
deadline = time.monotonic() + 20
while time.monotonic() < deadline and cmd("prompter_meeting_speakers")["job"]["busy"]:
    time.sleep(0.1)
job = cmd("prompter_meeting_speakers")["job"]
ok(job["done"] and not job["error"], f"команда: пересчёт завершён {job}")
r = cmd("prompter_meeting_speakers_edit", action="rename", id="ME", name="Дмитрий")
ok(any(s_.get("display") == "Дмитрий" for s_ in r["speakers"]), "команда: переименование")
ok(len(cmd("prompter_meeting_speakers_audio")["files"]) == 2, "команда: аудио участников")
try:
    cmd("prompter_meeting_speakers_x")
    raise AssertionError("неизвестная команда принята")
except ValueError:
    ok(True, "команда: неизвестное действие — ошибка")

# ---------- ревью 07.11: ручные правки текста, переназначение и объединение в аудио, атомарный экспорт ----------
def wav_tone(path, seconds, freq, amp=3000):
    t = np.arange(int(seconds * 16000)) / 16000
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.sin(2 * np.pi * freq * t) * amp).astype(np.int16).tobytes())


def rms(path, a, b):
    with wave.open(str(path)) as w:
        w.setpos(int(a * 16000))
        x = np.frombuffer(w.readframes(int((b - a) * 16000)), dtype=np.int16).astype(float)
    return float(np.sqrt((x * x).mean())) if len(x) else 0.0


rv_root = Path(tempfile.mkdtemp()) / "Встречи"
rv = service.MeetingFiles(rv_root, "R")
wav_tone(rv.me, 30, 300)
wav_tone(rv.them, 30, 600)
wav_tone(rv.mix, 30, 450)
(rv_root / "R.md").write_text("итог", encoding="utf-8")
(rv_root / "Расшифровки").mkdir(parents=True, exist_ok=True)
rv_lines = [{"who": "me", "speaker": "ME", "label": "Вы", "text": "Привет", "t": 1.0, "end": 3.0},
            {"who": "them", "speaker": "R1", "label": "Участник 1", "text": "Сколько стоит", "t": 5.0, "end": 9.0},
            {"who": "them", "speaker": "R2", "label": "Участник 2", "text": "А сроки", "t": 12.0, "end": 16.0},
            {"who": "me", "speaker": "ME", "label": "Вы", "text": "Это сказал сосед", "t": 20.0, "end": 23.0}]
for x in rv_lines:
    x.update(speaker0=x["speaker"], label0=x["label"], who0=x["who"])
rv_sp = [{"id": "ME", "label": "Вы"}, {"id": "R1", "label": "Участник 1"}, {"id": "R2", "label": "Участник 2"}]
service.write_lines_file(rv, {"version": 2, "speakers": rv_sp, "speakers_machine": rv_sp, "lines": rv_lines, "meeting": {}})
storage.write_rttm(rv.rttm, [Turn(5, 9, "R1"), Turn(12, 16, "R2")])
service.rewrite_txt(rv, rv_lines, service.SIGNATURE)
RREL = "Встречи/R.md"
r = service.edit(rv_root, RREL, "rename", id="R1", name="Пётр")
ok(not r["txt"]["manual"] and "[00:05] Пётр: Сколько стоит" in rv.txt.read_text(encoding="utf-8"), "без ручных правок — .txt собирается заново")
rv.txt.write_text(rv.txt.read_text(encoding="utf-8").replace("Сколько стоит", "Сколько стоит внедрение под ключ"), encoding="utf-8")
r = service.edit(rv_root, RREL, "rename", id="R1", name="Иван")
txt = rv.txt.read_text(encoding="utf-8")
ok(r["txt"]["manual"] and "[00:05] Иван: Сколько стоит внедрение под ключ" in txt, "ручная правка текста цела, подпись обновлена")
r = service.edit(rv_root, RREL, "reassign", t=12.0, end=12.0, to="R1")
txt = rv.txt.read_text(encoding="utf-8")
ok("[00:12] Иван: А сроки" in txt and "внедрение под ключ" in txt, "«Сказал(а)» при ручных правках: только подпись строки")
ed_r = edits.load(rv.speakers)
ok(ed_r["reassign"][-1]["end"] == 16.0, "переназначение хранит конец реплики (для аудио)")
service.edit(rv_root, RREL, "reassign", t=20.0, end=20.0, to="R2")      # реплика с микрофона — соседу
out = service.export_audio(rv_root, RREL)
fold = rv.folder / "Участники"
names = {x["id"]: fold / x["file"] for x in out["files"]}
ok(rms(names["R1"], 12.5, 15.5) > 1500 and "R2" in names and rms(names["R2"], 12.5, 15.5) < 50,
   "переназначенная реплика — в WAV нового участника, у прежнего её нет")
ok(rms(names["R2"], 20.5, 22.5) > 1500 and rms(names["ME"], 20.5, 22.5) < 50 and rms(names["ME"], 1.5, 2.5) > 1500,
   "реплика с микрофона, отданная другому, — в его WAV и не в «Вы»")
service.edit(rv_root, RREL, "merge", id="R1", to="ME")
out = service.export_audio(rv_root, RREL)
names = {x["id"]: fold / x["file"] for x in out["files"]}
ok(rms(names["ME"], 5.5, 8.5) > 2500 and "R1" not in names, f"объединение с «Вы»: речь участника — в дорожке «Вы» (rms {rms(names['ME'], 5.5, 8.5):.0f})")
before = sorted(x.name for x in fold.iterdir())
real_mix = tracks.mix_track
calls_mix = []


def broken_mix(*a_, **k_):
    calls_mix.append(1)
    if len(calls_mix) == 2:
        raise OSError("диск заполнен")
    return real_mix(*a_, **k_)


tracks.mix_track = broken_mix
try:
    service.export_audio(rv_root, RREL)
    raise AssertionError("сбой записи не всплыл")
except OSError:
    pass
finally:
    tracks.mix_track = real_mix
ok(sorted(x.name for x in fold.iterdir()) == before and not list(rv.folder.glob("Участники.new-*")),
   "сбой повторного экспорта: прежние WAV на месте, временная папка убрана")
# пересчёт при ручных правках: ваш .txt не меняется, новая разметка — рядом
os.environ["FAKE_TURNS"] = str(turns_file)
turns_file.write_text(json.dumps([{"start": 4.8, "end": 9.2, "speaker": "S0"}, {"start": 11.8, "end": 16.2, "speaker": "S1"}]))
storage.write_words(rv.words, {"me": [(0.0, 25.0, "Привет", [{"t": "Привет", "s": 1.0, "e": 2.0}])],
                               "them": [(0.0, 25.0, "Сколько стоит А сроки", [{"t": "Сколько", "s": 5.5, "e": 6.0}, {"t": "стоит", "s": 6.0, "e": 6.5},
                                                                               {"t": "А", "s": 12.5, "e": 12.7}, {"t": "сроки", "s": 12.7, "e": 13.2}])]})
mine_txt = rv.txt.read_text(encoding="utf-8")
out = service.rebuild(rv_root, RREL, {}, speaker=None)
ok(out["txt"]["manual"] and out["txt"]["alt"] and rv.txt.read_text(encoding="utf-8").split("\n\n", 1)[1] == mine_txt.split("\n\n", 1)[1]
   and Path(out["txt"]["alt"]).is_file(), "пересчёт: текст с ручными правками не тронут, новая разметка — в .участники.txt")

# сбой процесса — прежняя разметка «Вы / Собеседник», причина в итоге
os.environ["FAKE_DIAR"] = "error"
p2 = Prompter(FakeEngine(root))
p2._speaker = FakeSpeaker()
meeting(p2, "Сбой", dual=True)
for key in ("me", "them"):
    write_wav(Path(p2._track_paths[key]), 25)
write_wav(Path(p2._record_path), 25)
p2._post_transcribe()
ok([x["who"] for x in p2.lines] == ["them", "me"] and not any(x.get("label") for x in p2.lines)
   and "сломалось" in p2._speaker_note, f"сбой: текст как раньше, причина — {p2._speaker_note}")
os.environ["FAKE_DIAR"] = "ok"

# быстрый путь: режим «Вы и собеседники» — процесс не запускается
started = []
real_start = runner.start
runner.start = lambda *a, **k: started.append(1) or real_start(*a, **k)
try:
    p3 = Prompter(FakeEngine(root))
    p3._speaker = FakeSpeaker()
    meeting(p3, "Быстро", dual=True, mode="owner")
    for key in ("me", "them"):
        write_wav(Path(p3._track_paths[key]), 25)
    write_wav(Path(p3._record_path), 25)
    p3._post_transcribe()
    ok(not started and [x["who"] for x in p3.lines] == ["them", "me"], "«Вы и собеседники»: без процесса разбиения")
    raw = json.loads(Path(p3._transcript_path).with_suffix(".lines.json").read_text(encoding="utf-8"))
    ok(raw["version"] == 1 and raw["meeting"]["speaker_mode"] == "owner", ".lines.json прежней версии + режим")

    p4 = Prompter(FakeEngine(root))
    p4._speaker = FakeSpeaker()
    meeting(p4, "Без", dual=True, mode="none")
    for key in ("me", "them"):
        write_wav(Path(p4._track_paths[key]), 25)
    write_wav(Path(p4._record_path), 25)
    p4._post_transcribe()
    txt = Path(p4._transcript_path).read_text(encoding="utf-8")
    ok(not started and "] Речь: " in txt and "без разметки" in txt, "«Без разметки»: подпись «Речь»")
finally:
    runner.start = real_start

# одна дорожка, очная встреча: «Вы» по профилю (громкий отрезок)
p5 = Prompter(FakeEngine(root))
p5._speaker = FakeSpeaker()
meeting(p5, "Очная", dual=False, place="live")
write_wav(Path(p5._record_path), 25, amp=1500)
with wave.open(p5._record_path, "rb") as w:
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()
a[int(0.5 * 16000):int(2.5 * 16000)] *= 4                # ваш голос «громче»
with wave.open(p5._record_path, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(a.tobytes())
turns_file.write_text(json.dumps([{"start": 0.5, "end": 2.5, "speaker": "S1"}, {"start": 2.9, "end": 4.5, "speaker": "S0"},
                                  {"start": 5.9, "end": 7.5, "speaker": "S0"}]))
p5._post_transcribe()
got = [(x["who"], x.get("label"), x["text"]) for x in p5.lines]
ok(got == [("me", "Вы", "Добрый день."), ("them", "Собеседник", "Сколько стоит?"), ("them", "Собеседник", "А сроки?")],
   f"очная: «Вы» по профилю: {got}")

# очная встреча в режиме «Вы и собеседники» после записи — куски по профилю голоса
p6 = Prompter(FakeEngine(root, words=False))
p6._speaker = FakeSpeaker()
meeting(p6, "Очная2", dual=False, place="live", mode="owner")
write_wav(Path(p6._record_path), 25, amp=1500)
p6._post_transcribe()
ok([x["who"] for x in p6.lines] == ["them"], "очная без разбиения: кусок — по профилю голоса")

# ---------- 11. Очная встреча пишет WAV микрофона ----------
import audio_io  # noqa: E402

FRAME = 512
tone = (np.sin(np.arange(FRAME * 40) / 5) * 3000).astype(np.int16).tobytes()
chunks = [tone[i:i + FRAME * 2] for i in range(0, len(tone), FRAME * 2)]


class FakeMicCap:
    def __init__(self, *a, **k):
        self.i = 0

    def open(self):
        return "микрофон (тест)"

    def read(self):
        time.sleep(0.002)
        if self.i >= len(chunks):
            return b""
        self.i += 1
        return chunks[self.i - 1]

    def close(self):
        pass


class FakeVAD:
    def __init__(self, *_):
        pass

    def prob(self, _):
        return 0.9


sys.modules["listen"] = types.SimpleNamespace(SileroVAD=FakeVAD)
old_cap = audio_io.Capture
audio_io.Capture = FakeMicCap
try:
    p7 = Prompter(FakeEngine(root))
    p7._speaker = FakeSpeaker()
    p7._schedules_path = root / "s7.json"
    p7._summarize = lambda: None
    p7._post_transcribe = lambda: None
    st = p7.start(title="Очная запись", place="live", record=True, consent=True, deferred=False, speaker_mode="participants")
    ok(p7.meeting["record"] and p7.meeting["audio_source"] == "microphone" and not p7.meeting["dual"]
       and p7.meeting["speaker_mode"] == "participants", "очная встреча с записью: микрофон в WAV")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (p7._loop_thread is None or p7._loop_thread.is_alive()) and FakeMicCap is not None:
        if Path(p7._record_path).stat().st_size >= 44 + len(chunks) * FRAME * 2:
            break
        time.sleep(0.05)
    p7.stop(summary=False)
    if p7._post_thread is not None:
        p7._post_thread.join(5)
    with wave.open(p7._record_path, "rb") as w:
        ok(w.getnframes() >= len(chunks) * FRAME * 0.9, f"WAV очной встречи записан: {w.getnframes()} отсчётов")
finally:
    audio_io.Capture = old_cap

# Пустая расшифровка — тоже ручная правка.
empty_files = service.MeetingFiles(Path(tempfile.mkdtemp()), "empty")
empty_files.txt.parent.mkdir(parents=True)
empty_files.txt.write_text("# Встреча\n\n", encoding="utf-8")
old_rows = [{"who": "them", "text": "Удалено пользователем", "t": 1}]
result = service.rewrite_txt(empty_files, old_rows, previous=old_rows)
ok(result["manual"] and "Удалено пользователем" not in empty_files.txt.read_text(encoding="utf-8"), "пустая ручная расшифровка сохранена")

# Одновременные реплики на разных дорожках: переносится только выбранная.
overlap_edits = json.loads(json.dumps(edits.EMPTY))
edits.reassign(overlap_edits, 1, 3, "R2", source="R1", track="them")
remote = edits.apply_audio([Turn(1, 3, "R1")], overlap_edits, track="them")
mic = edits.apply_audio([Turn(1, 3, "ME")], overlap_edits, track="me")
ok(remote[0].speaker == "R2" and mic[0].speaker == "ME", "переназначение не затрагивает перекрывающийся микрофон")
same_track = edits.apply_audio([Turn(1, 3, "R1"), Turn(1, 3, "R3")], overlap_edits, track="them")
ok({x.speaker for x in same_track} == {"R2", "R3"}, "переназначение не затрагивает другого говорящего на той же дорожке")

overlap_root = Path(tempfile.mkdtemp()) / "Встречи"
overlap_files = service.MeetingFiles(overlap_root, "overlap")
wav_tone(overlap_files.me, 5, 300)
wav_tone(overlap_files.them, 5, 600)
wav_tone(overlap_files.mix, 5, 450)
overlap_files.txt.parent.mkdir(parents=True)
overlap_rows = [dict(who="me", who0="me", speaker="ME", speaker0="ME", label="Вы", label0="Вы", t=1, end=3, text="Я"),
                dict(who="them", who0="them", speaker="R1", speaker0="R1", label="Участник 1", label0="Участник 1", t=1, end=3, text="Он")]
overlap_speakers = [{"id": "ME", "label": "Вы"}, {"id": "R1", "label": "Участник 1"}, {"id": "R2", "label": "Участник 2"}]
service.write_lines_file(overlap_files, {"lines": overlap_rows, "speakers": overlap_speakers, "speakers_machine": overlap_speakers})
service.rewrite_txt(overlap_files, overlap_rows)
storage.write_rttm(overlap_files.rttm, [Turn(1, 3, "R1")])
service.edit(overlap_root, "overlap.md", "reassign", t=1, to="R2", source="R1")
after = service.read_lines_file(overlap_files)["lines"]
ok([x["speaker"] for x in after] == ["ME", "R2"], "переназначена выбранная реплика при одинаковом времени")
export = service.export_audio(overlap_root, "overlap.md")
paths = {x["id"]: overlap_files.folder / "Участники" / x["file"] for x in export["files"]}
ok(rms(paths["ME"], 1.2, 2.8) > 1000 and rms(paths["R2"], 1.2, 2.8) > 1000, "обе независимые дорожки сохранены после переназначения")

# Сбой на второй операции переименования возвращает прежнюю папку.
from unittest.mock import patch
swap_root = Path(tempfile.mkdtemp())
previous_export = swap_root / "Участники"
previous_export.mkdir()
(previous_export / "old.wav").write_bytes(b"old")
original_replace = Path.replace
def fail_new_swap(path, target):
    if path.name.startswith("Участники.new-"):
        raise OSError("ошибка замены")
    return original_replace(path, target)
try:
    with patch.object(Path, "replace", fail_new_swap):
        tracks.export_plan(swap_root, [])
    raise AssertionError("ошибка замены не передана")
except OSError:
    pass
ok((previous_export / "old.wav").read_bytes() == b"old" and not list(swap_root.glob("Участники.new-*")), "ошибка замены: старый экспорт восстановлен")

print(f"OK: разбиение по участникам — {N} проверок")
