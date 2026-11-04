"""Предварительные части по повторению блоков текста, без выдуманных границ.

Один повтор строки может быть рефреном внутри куплета. Только повтор блока
даёт кандидата припева; без музыкальных признаков название требует проверки.
Индексы ссылаются на исходные фразы, включая пустые строки.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher


def _norm(text):
    return re.sub(r"\W+", " ", str(text or "").casefold().replace("ё", "е")).strip()


def infer_sections(phrases):
    rows = phrases or []
    norm = [_norm(p.get("text")) for p in rows]
    marks = {}
    group = 0
    for i in range(len(rows)):
        if i in marks or not norm[i]:
            continue
        for j in range(i + 2, len(rows)):
            if j in marks:
                continue
            size = 0
            while i + size < j and j + size < len(rows):
                a, b = i + size, j + size
                if a in marks or b in marks or not norm[a] or not norm[b]:
                    break
                if SequenceMatcher(None, norm[a], norm[b]).ratio() < .84:
                    break
                size += 1
            if size >= 2 and sum(len(norm[k].split()) for k in range(i, i + size)) >= 6:
                group += 1
                for occurrence, start in enumerate((i, j), 1):
                    for k in range(start, start + size):
                        marks[k] = (group, occurrence)
                break
    out = []
    for index, phrase in enumerate(rows):
        if not norm[index]:
            continue
        repeat = marks.get(index)
        previous = out[-1] if out else None
        gap = int(phrase.get("start_ms") or 0) - (previous["end_ms"] if previous else 0)
        if previous and previous["_repeat"] == repeat and gap < 2500 and previous["phrase_indexes"][-1] == index - 1:
            previous["phrase_indexes"].append(index)
            previous["end_ms"] = int(phrase.get("end_ms") or 0)
            continue
        out.append({"section_id": f"section-{index}", "kind": "chorus" if repeat else "unknown",
                    "label": "Возможный припев" if repeat else f"Часть {len(out) + 1}",
                    "start_ms": int(phrase.get("start_ms") or 0), "end_ms": int(phrase.get("end_ms") or 0),
                    "phrase_indexes": [index], "confidence_level": "candidate" if repeat else "unknown",
                    "review_state": "needs_review", "repeat_group_id": f"repeat-{repeat[0]}" if repeat else None,
                    "occurrence": repeat[1] if repeat else None, "_repeat": repeat,
                    "evidence": "Повтор блока текста; проверьте по музыке" if repeat else "Недостаточно признаков для названия"})
    for section in out:
        section.pop("_repeat")
    return out
