"""Правки участников — отдельно от машинного результата (правило AGENTS.md: повторная обработка не затирает правки).

<имя>.speakers.json рядом с .txt:
  {"version": 1, "names": {"R1": "Иван"}, "merged": {"R3": "R1"}, "reassign": [{"t", "end", "to"}],
   "not_transferred": [{"kind", "id", "value"}]}
При пересчёте участников правки переносятся по совпадению времени речи (map_speakers), голосовые векторы
не нужны и не хранятся. Что не перенеслось — в not_transferred, это видно в проигрывателе.
"""
import json
import re
from pathlib import Path

from .schema import Turn

EMPTY = {"version": 1, "names": {}, "merged": {}, "reassign": [], "not_transferred": []}
NAME_RX = re.compile(r"[\x00-\x1f]")


def path_for(transcript_txt):
    return Path(transcript_txt).with_suffix(".speakers.json")


def load(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    out = json.loads(json.dumps(EMPTY))
    if isinstance(d, dict):
        for k in ("names", "merged"):
            if isinstance(d.get(k), dict):
                out[k] = {str(a): str(b) for a, b in d[k].items() if str(b).strip()}
        for k in ("reassign", "not_transferred"):
            if isinstance(d.get(k), list):
                out[k] = [x for x in d[k] if isinstance(x, dict)]
    return out


def save(path, edits):
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(edits, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def clean_name(name):
    name = NAME_RX.sub(" ", str(name or "")).strip()
    return re.sub(r"\s+", " ", name)[:60]


def _root(edits, sid):
    seen = set()
    while sid in edits["merged"] and sid not in seen:
        seen.add(sid)
        sid = edits["merged"][sid]
    return sid


def rename(edits, sid, name):
    name = clean_name(name)
    sid = _root(edits, str(sid))
    if name:
        edits["names"][sid] = name
    else:
        edits["names"].pop(sid, None)
    return edits


def merge(edits, src, dst):
    src, dst = _root(edits, str(src)), _root(edits, str(dst))
    if src == dst:
        return edits
    edits["merged"][src] = dst
    if src in edits["names"] and dst not in edits["names"]:
        edits["names"][dst] = edits["names"][src]
    edits["names"].pop(src, None)
    return edits


def reassign(edits, t, end, to, source=None, track=None):
    t, end = float(t), float(end if end is not None else t)
    edits["reassign"] = [x for x in edits["reassign"] if not (abs(float(x.get("t", -1)) - t) < 0.05 and x.get("source") == source and x.get("track") == track)]
    item = {"t": round(t, 2), "end": round(max(end, t), 2), "to": str(to)}
    if source is not None: item["source"] = str(source)
    if track is not None: item["track"] = str(track)
    edits["reassign"].append(item)
    return edits


def apply(lines, speakers, edits):
    """-> (реплики, участники) с подписями из правок. Исходные списки не меняются."""
    base = {s["id"]: s.get("label") or s["id"] for s in speakers or []}
    out_speakers = []
    for s in speakers or []:
        sid = s["id"]
        if sid in edits["merged"]:
            continue
        row = dict(s)
        if sid in edits["names"]:
            row["name"] = edits["names"][sid]
        row["display"] = edits["names"].get(sid) or base.get(sid, sid)
        out_speakers.append(row)
    known = {s["id"] for s in out_speakers}
    display = {s["id"]: s["display"] for s in out_speakers}
    out = []
    for x in lines:
        row = dict(x)
        sid = row.get("speaker")
        if sid:
            for r in edits["reassign"]:
                if r.get("source") and str(row.get("speaker0") or row.get("speaker")) != r["source"]:
                    continue
                a, b = float(r.get("t", 0)), float(r.get("end", r.get("t", 0)))
                end = float(row.get("end") or row.get("t") or 0)
                start = float(row.get("t") or 0)
                if abs(start - a) < 0.15 or (min(end, b) - max(start, a)) > 0.5 * max(end - start, 1e-3):
                    sid = str(r.get("to"))
                    row["edited"] = True
            sid = _root(edits, sid)
            if sid in known:
                row["speaker"] = sid
                row["label"] = display[sid]
                row["who"] = "me" if sid == "ME" else ("them" if row.get("who") != "screen" else "screen")
        out.append(row)
    return out, out_speakers


def map_speakers(old, new, min_share=0.5):
    """Старый id -> новый id по совпадению времени речи (без голосовых векторов)."""
    new = sorted(new, key=lambda t: t.start)
    ov, total, j = {}, {}, 0
    for a in sorted(old, key=lambda t: t.start):
        total[a.speaker] = total.get(a.speaker, 0.0) + (a.end - a.start)
        while j < len(new) and new[j].end <= a.start:
            j += 1
        k = j
        while k < len(new) and new[k].start < a.end:
            b = new[k]
            d = min(a.end, b.end) - max(a.start, b.start)
            if d > 0:
                ov[(a.speaker, b.speaker)] = ov.get((a.speaker, b.speaker), 0.0) + d
            k += 1
    out, used = {}, set()
    for (o, n), d in sorted(ov.items(), key=lambda kv: kv[1], reverse=True):
        if o not in out and n not in used and d >= min_share * total[o]:
            out[o] = n
            used.add(n)
    return out


def transfer(edits, old_turns, new_turns):
    """Правки к новым id после пересчёта. «Вы» (ME) — всегда ME. -> новые правки."""
    mapping = map_speakers([t for t in old_turns if t.speaker != "ME"], [t for t in new_turns if t.speaker != "ME"])
    mapping["ME"] = "ME"
    out = json.loads(json.dumps(EMPTY))
    lost = list(edits.get("not_transferred") or [])
    for sid, name in edits["names"].items():
        if sid in mapping:
            out["names"][mapping[sid]] = name
        else:
            lost.append({"kind": "name", "id": sid, "value": name})
    for src, dst in edits["merged"].items():
        if src in mapping and dst in mapping and mapping[src] != mapping[dst]:
            out["merged"][mapping[src]] = mapping[dst]
    for r in edits["reassign"]:
        to = str(r.get("to"))
        source = r.get("source")
        if to in mapping and (not source or source in mapping):
            updated = dict(r, to=mapping[to])
            if source: updated["source"] = mapping[source]
            out["reassign"].append(updated)
        else:
            lost.append({"kind": "reassign", "id": to, "value": r.get("t")})
    out["not_transferred"] = lost[-50:]
    return out


def apply_audio(turns, edits, track=None):
    """Интервалы для аудио участников: объединения и переназначения. Переназначенная часть интервала (по времени
    реплики t..end) переходит новому участнику, остаток — прежнему."""
    out = [Turn(t.start, t.end, t.speaker) for t in turns]
    for r in edits["reassign"]:
        if r.get("track") and r["track"] != track:
            continue
        a, b = float(r.get("t", 0)), float(r.get("end", r.get("t", 0)))
        if b <= a:
            continue
        to = _root(edits, str(r.get("to")))
        nxt = []
        for t in out:
            if r.get("source") and t.speaker != r["source"]:
                nxt.append(t)
                continue
            if t.end <= a or t.start >= b or t.speaker == to:
                nxt.append(t)
                continue
            if t.start < a:
                nxt.append(Turn(t.start, a, t.speaker))
            nxt.append(Turn(max(t.start, a), min(t.end, b), to))
            if t.end > b:
                nxt.append(Turn(b, t.end, t.speaker))
        out = nxt
    return sorted((Turn(t.start, t.end, _root(edits, t.speaker)) for t in out), key=lambda t: (t.start, t.end))


turns_for_audio = apply_audio
