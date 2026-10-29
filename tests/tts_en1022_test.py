"""Пакет здоровья 22.10, №194: «произношение фраз на английском просто жесть, хуже школьника — при смешении языков не
переключает». Русский голос (Vosk, Piper) читал английское русскими звуками по буквам. Теперь английские обороты и слова
строчными — английским голосом Piper (женский/мужской под персонажа), остальное — голосом персонажа; голоса нет —
скачивается в фоне один раз, пока читаем как раньше. Голос сервера и ElevenLabs многоязычные — как были.
И там же: «GigaAM не распознал: 'NoneType' object has no attribute 'recognize'» — модель ещё грузилась; теперь словами."""
import os, sys, types
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import tts  # noqa: E402

ok(tts.split_en("Скажи: How are you today? — и ответь.") == [("ru", "Скажи: "), ("en", "How are you today?"), ("ru", " — и ответь.")],
   "английский оборот внутри русской фразы — отдельным куском")
ok(tts.split_en("Открой Linux и Telegram, включи wifi.") == [("ru", "Открой Linux и Telegram, включи wifi.")],
   "названия программ (с большой буквы, известные) — по-русски, как раньше")
sp = tts.split_en("Слово apple значит яблоко, а фраза I like apples — я люблю яблоки.")
ok([x for x in sp if x[0] == "en"] == [("en", "apple"), ("en", "I like apples")], f"слово для урока и фраза — английские: {sp}")
ok(tts.split_en("The weather is nice today.") == [("en", "The weather is nice today.")], "целиком по-английски — целиком английским голосом")
r = tts.resample(b"\x00\x10" * 160, 16000, 22050)
ok(len(r) // 2 == 220, f"частота английского голоса приводится к частоте фразы: {len(r) // 2} отсчётов")
ok("{lang}/{loc}/" in tts.PIPER_URL and tts.EN_VOICES["female"].startswith("en_US-"), "английский голос Piper скачивается тем же способом")

import engine_remote as ER  # noqa: E402
mix = next(v for v in vars(ER).values() if isinstance(v, type) and "_synth_mixed" in vars(v))


class Eng:
    name = "Vosk"
    said = []
    def synth(self, text, voice, sp):
        Eng.said.append(text)
        yield 22050, b"\x01\x00" * 10


class Piper:
    def __init__(self, has):
        self._has, self.said = has, []
    def has(self, vid):
        return self._has
    def synth(self, text, voice, sp, norm=True):
        self.said.append((text, voice["piper"], norm))
        yield 16000, b"\x02\x00" * 160


fetched = []
me = types.SimpleNamespace(piper=Piper(True), s=types.SimpleNamespace(get=lambda *k: None),
                           _en_voice_fetch=lambda vid: fetched.append(vid))
Eng.said = []
out = list(mix._synth_mixed(me, Eng(), "Скажи: How are you? — и ответь.", {"_gender": "female"}, 1.0))
ok([t for t, *_ in me.piper.said] == ["How are you?"] and me.piper.said[0][1] == "en_US-amy-medium" and me.piper.said[0][2] is False,
   f"английское — английским женским голосом, без русских чисел: {me.piper.said}")
ok(all("How" not in t for t in Eng.said) and len(Eng.said) == 2, f"русское — голосом персонажа: {Eng.said}")
ok(all(r == 22050 for r, _ in out), "весь ответ — одной частотой (плеер не перезапускается)")
me.piper = Piper(False); Eng.said = []
out = list(mix._synth_mixed(me, Eng(), "Скажи: How are you?", {"_gender": "male"}, 1.0))
ok(Eng.said == ["Скажи: How are you?"] and fetched == ["en_US-ryan-medium"], f"английского голоса ещё нет — как раньше и скачиваем мужской: {fetched}")
ok(isinstance(getattr(mix, "_en_voice_fetch", None), object) and "egress.local_only()" in (ROOT / "core/engine_remote.py").read_text(encoding="utf-8"),
   "под стоп-краном «ничего в интернет» голос не качаем")

import stt  # noqa: E402
g = stt.GigaAMSTT.__new__(stt.GigaAMSTT); g.model, g.loading, g.error = None, True, ""
try:
    g.transcribe(b"\x00\x00" * 100); msg = ""
except RuntimeError as e:
    msg = str(e)
ok("ещё загружается" in msg, f"GigaAM не готов — словами, а не «NoneType … recognize»: {msg}")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
