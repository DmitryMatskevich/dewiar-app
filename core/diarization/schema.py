"""Простые типы разбиения по участникам."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Turn:
    """Интервал речи одного говорящего (секунды от начала записи)."""
    start: float
    end: float
    speaker: str


@dataclass
class Word:
    text: str
    start: float
    end: float
    speaker: str | None = None
    overlap: bool = False


@dataclass
class Utterance:
    speaker: str | None
    start: float
    end: float
    words: list = field(default_factory=list)
    text: str = ""
    overlap: bool = False


def turns_from_dicts(rows):
    out = []
    for r in rows or []:
        try:
            s, e = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            out.append(Turn(s, e, str(r.get("speaker") or "S0")))
    return sorted(out, key=lambda t: (t.start, t.end))


def turns_to_dicts(turns):
    return [{"start": round(t.start, 3), "end": round(t.end, 3), "speaker": t.speaker} for t in turns]
