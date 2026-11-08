"""Кто есть кто: порядок появления, центроиды голоса (CAM++ из speaker.py), слияние раздробленных кластеров,
кластер владельца по его профилю. Голосовые векторы участников не сохраняются."""
import numpy as np

from .schema import Turn


def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def first_appearance(turns):
    seen = []
    for t in sorted(turns, key=lambda t: t.start):
        if t.speaker not in seen:
            seen.append(t.speaker)
    return seen


def relabel(turns, mapping):
    return [Turn(t.start, t.end, mapping.get(t.speaker, t.speaker)) for t in turns]


def renumber(turns, prefix="R", start=1):
    """Метки движка (S0, S7, …) -> R1, R2, … по порядку первого появления."""
    order = first_appearance(turns)
    return relabel(turns, {s: f"{prefix}{n}" for n, s in enumerate(order, start)})


def speech_seconds(turns):
    out = {}
    for t in turns:
        out[t.speaker] = out.get(t.speaker, 0.0) + (t.end - t.start)
    return out


def centroids(turns, embed, per_speaker=8, min_len=1.5):
    """embed(start, end) -> вектор голоса отрезка или None. По per_speaker самым длинным интервалам."""
    by_spk = {}
    for t in turns:
        by_spk.setdefault(t.speaker, []).append(t)
    out = {}
    for spk, ts in by_spk.items():
        longest = sorted((t for t in ts if t.end - t.start >= min_len),
                         key=lambda t: t.end - t.start, reverse=True)[:per_speaker]
        vecs = []
        for t in longest:
            v = embed(t.start, t.end)
            if v is not None:
                v = np.asarray(v, dtype=np.float32)
                vecs.append(v / (np.linalg.norm(v) + 1e-9))
        if vecs:
            c = np.mean(vecs, axis=0)
            out[spk] = c / (np.linalg.norm(c) + 1e-9)
    return out


def merge_close_clusters(cents, threshold=0.80):
    """id -> id: кластеры с косинусом центроидов ≥ threshold сливаются (один голос, раздробленный на два)."""
    ids = list(cents)
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if cosine(cents[a], cents[b]) >= threshold:
                parent[find(b)] = find(a)
    return {i: find(i) for i in ids}


def owner_clusters(cents, owner, threshold, join=0.80):
    """Кластер владельца: самый похожий на профиль (≥ threshold). Второй кластер присоединяется, только если и он
    очень похож (≥ join): похожий чужой голос даёт 0,55–0,7, и порог threshold тут не годится (замер на стенде).
    -> (ids, лучшая похожесть)."""
    if owner is None or not cents:
        return [], 0.0
    scores = {s: cosine(c, owner) for s, c in cents.items()}
    best_id = max(scores, key=scores.get)
    best = scores[best_id]
    if best < threshold:
        return [], best
    return [best_id] + [s for s, v in scores.items() if s != best_id and v >= max(threshold, join)], best
