"""Сборка расшифровки по участникам из кусков распознавания и интервалов диаризации.

Кусок распознавания — (начало, конец, текст) или (начало, конец, текст, слова), слова — [{"t", "s", "e"}] в секундах
от начала записи (GigaAM через onnx-asr with_timestamps). Без слов (Vosk, удалённое распознавание) кусок целиком
получает говорящего с наибольшим перекрытием.

Реплика — {"who": "me"|"them", "speaker": id, "label": подпись, "text", "t", "end"[, "overlap"]}: поле who остаётся
для старого кода и окна, speaker/label — новое.
"""
from .align import assign_speakers, group, segment_speaker, smooth
from .identify import centroids, first_appearance, merge_close_clusters, owner_clusters, relabel, renumber, speech_seconds
from .schema import Word

import numpy as np

OWNER = "ME"


def _parts(seg):
    start, end, text = float(seg[0]), float(seg[1]), str(seg[2] or "").strip()
    words = seg[3] if len(seg) > 3 else None
    return start, end, text, words


def _line(who, speaker, label, text, t, end, overlap=False):
    row = {"who": who, "speaker": speaker, "label": label, "text": text, "t": round(float(t), 1), "end": round(float(end), 2)}
    if overlap:
        row["overlap"] = True
    return row


def _track_lines(segs, turns, who_of):
    """Реплики одной дорожки: слова — по говорящим, куски без слов — целиком."""
    words, rows = [], []
    for seg in segs:
        start, end, text, ws = _parts(seg)
        if not text:
            continue
        if ws:
            for w in ws:
                s, e = float(w["s"]), float(w["e"])
                words.append(Word(str(w["t"]), s, max(e, s + 0.02)))
        else:
            spk, ov = segment_speaker(start, end, turns)
            rows.append((spk, start, end, text, ov))
    if words:
        words.sort(key=lambda w: w.start)
        assign_speakers(words, turns)
        smooth(words)
        rows += [(u.speaker, u.start, u.end, u.text, u.overlap) for u in group(words)]
    out = []
    for spk, start, end, text, ov in sorted(rows, key=lambda r: r[1]):
        who = who_of(spk)
        out.append((who, spk, start, end, text, ov))
    return out


def owner_lines(segs, max_pause=1.5):
    """Дорожка микрофона (только ваш голос): реплики по паузам между словами; кусок без слов — целиком.
    Кусок распознавания до 22 с иначе склеивал бы две ваши реплики с чужой между ними. -> [(начало, конец, текст)]."""
    out = []
    for seg in segs:
        start, end, text, ws = _parts(seg)
        if not text:
            continue
        if not ws:
            out.append((start, end, text))
            continue
        words = [Word(str(w["t"]), float(w["s"]), max(float(w["e"]), float(w["s"]) + 0.02), OWNER) for w in ws]
        out += [(u.start, u.end, u.text) for u in group(words, max_pause)]
    return sorted(out)


def _speakers(turns, labels, extra=None):
    secs = speech_seconds(turns)
    out = list(extra or [])
    for sid in first_appearance(turns):
        if sid == OWNER and any(x["id"] == OWNER for x in out):
            continue
        out.append({"id": sid, "label": labels[sid], "source": "owner_profile" if sid == OWNER else "diarization",
                    "speech_sec": round(secs.get(sid, 0.0), 1)})
    return out


def limit_speakers(turns, embed, cap, anchors=None):
    """Голосов больше, чем участников (cap): самый маленький кластер вливается в ближайший по голосу, по одному.
    Отколовшийся кусок голоса обычно мал, а реальные участники — большие кластеры: их не склеиваем между собой
    (повторная кластеризация с жёстким числом склеивала похожие голоса, в том числе с владельцем)."""
    if not cap or embed is None:
        return turns
    ids = set(t.speaker for t in turns)
    if len(ids) <= cap:
        return turns
    cents = centroids(turns, embed, min_len=1.0)
    anchors = {k: v / (np.linalg.norm(v) + 1e-9) for k, v in (anchors or {}).items() if v is not None}
    secs = speech_seconds(turns)
    mapping = {s: s for s in ids}
    alive = set(ids)
    while len(alive) > cap:
        small = min(alive, key=lambda s: secs.get(s, 0.0))
        others = [s for s in alive if s != small]
        if small in cents and (any(o in cents for o in others) or anchors):
            # цель — самый похожий голос: другой кластер или «якорь» вне предела (например, «Вы»)
            pool = {o: cents[o] for o in others if o in cents}
            pool.update(anchors)
            target = max(pool, key=lambda o: float(np.dot(cents[small], pool[o])))
            if target in anchors:
                for k, v in mapping.items():
                    if v == small:
                        mapping[k] = target
                secs.pop(small, None)
                alive.discard(small)
                continue
        else:                                             # без вектора — к соседу по времени
            last = [t for t in sorted(turns, key=lambda t: t.start) if mapping[t.speaker] == small]
            near = [t for t in turns if mapping[t.speaker] != small and last and abs(t.end - last[0].start) < 5.0]
            target = mapping[near[0].speaker] if near else max(others, key=lambda o: secs.get(o, 0.0))
        for k, v in mapping.items():
            if v == small:
                mapping[k] = target
        if small in cents and target in cents:
            w1, w2 = secs.get(small, 0.0), secs.get(target, 0.0)
            c = cents[small] * w1 + cents[target] * w2
            cents[target] = c / (np.linalg.norm(c) + 1e-9)
        secs[target] = secs.get(target, 0.0) + secs.pop(small, 0.0)
        alive.discard(small)
    out = relabel(turns, mapping)
    kept = [t for t in out if t.speaker not in anchors]
    return sorted(renumber(kept) + [t for t in out if t.speaker in anchors], key=lambda t: (t.start, t.end))


def _merge_if_auto(turns, embed, auto, threshold):
    if not (auto and embed is not None and len(set(t.speaker for t in turns)) > 1):
        return turns, None
    cents = centroids(turns, embed)
    remap = merge_close_clusters(cents, threshold)
    if len(set(remap.values())) < len(remap):
        turns = renumber(relabel(turns, remap))
        return turns, None
    return turns, cents


def build_dual(me_segs, them_segs, turns, embed=None, auto=True, merge_threshold=0.80, cap=0):
    """Онлайн-встреча с двумя дорожками: «Вы» — микрофон, участники — диаризация дорожки собеседника.
    cap — предел числа голосов (список «Участники»). -> (реплики, участники, итоговые интервалы)."""
    turns = limit_speakers(renumber(turns), embed, cap)
    turns, _ = _merge_if_auto(turns, embed, auto, merge_threshold)
    order = first_appearance(turns)
    labels = {s: f"Участник {n}" for n, s in enumerate(order, 1)}
    if len(order) == 1:
        labels[order[0]] = "Собеседник"
    lines = [_line("me", OWNER, "Вы", text, start, end) for start, end, text in owner_lines(me_segs)]
    fallback = order[0] if len(order) == 1 else None
    for _who, spk, start, end, text, ov in _track_lines(them_segs, turns, lambda s: "them"):
        spk = spk or fallback
        lines.append(_line("them", spk, labels.get(spk, "Собеседник"), text, start, end, ov))
    lines.sort(key=lambda x: (x["t"], 0 if x["who"] == "them" else 1))
    speakers = _speakers(turns, labels, extra=[{"id": OWNER, "label": "Вы", "source": "mic_track"}])
    return lines, speakers, turns


def build_single(segs, turns, embed=None, owner=None, owner_threshold=0.55, auto=True, merge_threshold=0.80, cap=0):
    """Одна дорожка (очная встреча или онлайн без микрофона): «Вы» — по записанному профилю владельца.

    Порядок важен: сначала «Вы» (лучший кластер и куски, очень похожие на профиль, — профиль точнее центроида
    короткого куска), затем предел числа голосов и слияние — только среди остальных. Иначе короткий кусок вашей
    речи вливался в похожий чужой голос."""
    turns = renumber(turns)
    anchor = None
    if embed is not None and owner is not None:
        cents = centroids(turns, embed, min_len=1.0)
        mine, _best = owner_clusters(cents, owner, owner_threshold)
        if mine:
            turns = relabel(turns, {s: OWNER for s in mine})
            vecs = [cents[s] for s in mine if s in cents]
            anchor = np.mean(vecs, axis=0) if vecs else owner   # ваш голос в этой записи, а не только профиль
    mine_turns = [t for t in turns if t.speaker == OWNER]
    rest = [t for t in turns if t.speaker != OWNER]
    if rest:
        rest = limit_speakers(rest, embed, max(1, cap - 1) if cap and mine_turns else cap,
                              anchors={OWNER: anchor} if anchor is not None else None)
        mine_turns += [t for t in rest if t.speaker == OWNER]
        rest = [t for t in rest if t.speaker != OWNER]
        rest, _ = _merge_if_auto(rest, embed, auto, merge_threshold)
        order = first_appearance(rest)
        rest = relabel(rest, {s: f"R{n}" for n, s in enumerate(order, 1)})
    turns = sorted(mine_turns + rest, key=lambda t: (t.start, t.end))
    order = [s for s in first_appearance(turns) if s != OWNER]
    labels = {s: f"Участник {n}" for n, s in enumerate(order, 1)}
    if len(order) == 1:
        labels[order[0]] = "Собеседник"
    labels[OWNER] = "Вы"
    lines = []
    for _who, spk, start, end, text, ov in _track_lines(segs, turns, lambda s: "me" if s == OWNER else "them"):
        who = "me" if spk == OWNER else "them"
        lines.append(_line(who, spk, labels.get(spk, "Собеседник"), text, start, end, ov))
    return lines, _speakers(turns, labels), turns


def owner_split(segs, who_of):
    """Режим «Вы и собеседники» для одной дорожки очной встречи: who_of(начало, конец) -> "me" | "them"."""
    out = []
    for seg in segs:
        start, end, text, _ws = _parts(seg)
        if text:
            who = who_of(start, end)
            out.append({"who": who, "text": text, "t": round(start, 1)})
    return out


def expected_speakers(meeting, cfg, single_track=False):
    """(точное число, предел) голосов для движка; 0 — авто. Пользователь указывает участников без себя:
    точное — из окна встречи или настроек, предел — по списку «Участники» (имена через запятую, «Вы» не считается).
    single_track — диаризуется дорожка и с вашим голосом (очная встреча, онлайн без микрофона): +1."""
    def as_int(v):
        try:
            return max(0, min(20, int(v or 0)))
        except (TypeError, ValueError):
            return 0
    exact = as_int((meeting or {}).get("expected_speakers")) or as_int((cfg or {}).get("expected_speakers"))
    names = (meeting or {}).get("participants") or []
    if isinstance(names, str):
        names = [x for x in names.replace(";", ",").split(",")]
    names = [str(x).strip() for x in names if str(x).strip()]
    cap = len([x for x in names if x.lower() not in {"вы", "я", "мы"}])
    if single_track:                                     # в общей дорожке есть и ваш голос
        exact, cap = (exact + 1 if exact else 0), (cap + 1 if cap else 0)
    return exact, cap
