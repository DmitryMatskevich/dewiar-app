"""Файлы разбиения рядом с расшифровкой: <имя>.diarization.rttm (сырые интервалы) и <имя>.words.json
(слова с временем — тяжёлое, отдельно от .lines.json)."""
import json
from pathlib import Path

from .schema import Turn


def rttm_path(transcript_txt):
    return Path(transcript_txt).with_suffix(".diarization.rttm")


def words_path(transcript_txt):
    return Path(transcript_txt).with_suffix(".words.json")


def write_rttm(path, turns, uri="meeting"):
    uri = str(uri or "meeting").replace(" ", "_")
    rows = [f"SPEAKER {uri} 1 {t.start:.3f} {t.end - t.start:.3f} <NA> <NA> {t.speaker} <NA> <NA>"
            for t in sorted(turns, key=lambda t: t.start)]
    p = Path(path)
    p.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return p


def read_rttm(path):
    out = []
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            parts = ln.split()
            if len(parts) >= 8 and parts[0] == "SPEAKER":
                s, d = float(parts[3]), float(parts[4])
                out.append(Turn(s, s + d, parts[7]))
    except (OSError, ValueError):
        return []
    return out


def write_words(path, tracks):
    """tracks: {"me"|"them"|"mix": [(начало, конец, текст, слова?)]}."""
    data = {"version": 1, "tracks": {}}
    for key, segs in (tracks or {}).items():
        data["tracks"][key] = [{"start": round(float(s[0]), 2), "end": round(float(s[1]), 2), "text": s[2],
                                "words": (s[3] if len(s) > 3 and s[3] else None)} for s in segs]
    p = Path(path)
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return p


def read_words(path):
    """-> {"me"|"them"|"mix": [(начало, конец, текст, слова)]} или {}."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        out = {}
        for key, rows in (data.get("tracks") or {}).items():
            out[key] = [(float(r["start"]), float(r["end"]), str(r.get("text") or ""), r.get("words") or None)
                        for r in rows]
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def has_words(tracks):
    return any(s[3] for segs in tracks.values() for s in segs if len(s) > 3)
