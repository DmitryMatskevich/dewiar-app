"""№213 (пакет 23.10, Windows, голос Vosk TTS): «когда Лира говорит, слышен какой-то ещё голос на английском». С 0.39.4
английское читает английский голос Piper — но в него попадали и названия посреди русской речи: «PDF, DOCX», «Telegram
Desktop», «Google Chrome», «docx», «deepseek-chat». Теперь английским голосом — только настоящие английские обороты и
слова (урок: «I live in…», «apple», «How are you?»), а названия, аббревиатуры, форматы и имена моделей — голосом персонажа."""
import os, sys
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import tts  # noqa: E402

en = lambda t: [x for lang, x in tts.split_en(t) if lang == "en"]
for t in ("Можно прикрепить файл PDF, DOCX или TXT — нажмите Ctrl+V.", "Откройте Telegram Desktop или Google Chrome.",
          "Формат docx подходит.", "Модель deepseek-chat работает.", "Сайт dewiar.pro и github.com, модель gpt-4o.", "Нажмите OK."):
    ok(en(t) == [], f"название, а не английский — голосом персонажа: «{t}» → {en(t)}")
for t, want in (("Скажите I live in Москва and I like it.", ["I live in", "and I like it."]), ("Слово apple значит яблоко.", ["apple"]),
                ("Повтори: How are you?", ["How are you?"]), ("Good morning! Как дела?", ["Good morning!"]),
                ("В «Близких» есть Drag and drop.", ["Drag and drop."])):
    ok(en(t) == want, f"урок английского — английским голосом: «{t}» → {en(t)}")

if fails:
    raise SystemExit(1)
