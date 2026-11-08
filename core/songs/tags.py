"""Automatic song tags from the recognized text.

The classifier is deliberately conservative: only a clear lexical signal can
cross the 70% threshold.  Scores are saved with the card so the user can see
why a tag was assigned and edit the result afterwards.
"""
from __future__ import annotations

import re

from .store import TAG_OPTIONS


_SIGNALS = {
    "Компания": ("друз", "компани", "вместе", "товарищ", "гульн", "праздник"),
    "Италия": ("итал", "рим", "венеци", "милан", "сицили", "неапол", "итальян"),
    "Любовь": ("любов", "любим", "сердц", "поцелу", "красавиц", "нежн", "обнима", "разлук"),
    "Шансон": ("тюрьм", "зон", "блат", "лагер", "этап", "братв", "кореш", "кабак", "вокзал"),
    "Бард": ("костр", "гитар", "поход", "турист", "палатк", "дорог", "странник"),
    "Запад": ("америк", "лондон", "париж", "британ", "запад", "штат", "голливуд"),
    "Грусть": ("груст", "слез", "плач", "один", "тоск", "боль", "обидно", "безнадеж", "устал", "прощал"),
    "Юмор": ("смешн", "шут", "хохот", "юмор", "дурак", "прикол", "анекдот", "чудак"),
    "Военные": ("войн", "солдат", "фронт", "арм", "бойц", "окоп", "шинел", "полк", "автомат", "побед"),
    "Дворовые": ("двор", "подъезд", "пацан", "лавочк", "район", "улиц", "дворов", "подворот"),
    "Народные": ("народ", "казак", "гармош", "ой ", "эх ", "гуляй", "родн", "степ"),
    "Советские": ("совет", "ссср", "пионер", "комсомол", "октябр", "ленин", "родин", "побед"),
    "Детские": ("детск", "ребят", "малыш", "ребен", "дети", "колыбел", "мама", "сказк"),
}


def infer_tags(phrases, title="", artist=""):
    text = " ".join(str(p.get("text") or "") for p in phrases or []).casefold().replace("ё", "е")
    context = f"{title} {artist}".casefold().replace("ё", "е")
    tags, scores = [], {}
    for tag in TAG_OPTIONS:
        signals = _SIGNALS.get(tag, ())
        hits = sum(len(re.findall(re.escape(signal), text)) for signal in signals)
        # A title/artist marker is useful evidence but never enough alone for
        # a thematic tag; it only boosts a signal already found in the lyrics.
        context_hits = sum(1 for signal in signals if signal.strip() and signal in context)
        if hits + context_hits == 0:
            continue
        probability = min(0.99, 0.70 + 0.08 * min(3, hits - 1) + 0.04 * min(2, context_hits))
        probability = round(probability, 2)
        scores[tag] = probability
        if probability >= 0.70:
            tags.append(tag)
    return tags, scores

