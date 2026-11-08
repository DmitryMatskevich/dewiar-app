"""Слова ↔ говорящие: назначение по перекрытию, сглаживание «мерцания», группировка в реплики."""
from .schema import Utterance


def _nearest_speaker(turns, w, max_gap):
    best, best_gap = None, max_gap
    for t in turns:                                    # редко: только слова в паузах между интервалами
        gap = max(t.start - w.end, w.start - t.end, 0.0)
        if gap <= best_gap:
            best, best_gap = t.speaker, gap
    return best


def assign_speakers(words, turns, max_gap=0.5):
    """Слову — говорящий с наибольшим перекрытием; overlap — второй перекрывает больше 30 % слова.
    Слово вне интервалов — ближайший в пределах max_gap, иначе говорящий предыдущего слова."""
    turns = sorted(turns, key=lambda t: t.start)
    j, prev = 0, None
    for w in sorted(words, key=lambda x: x.start):
        while j < len(turns) and turns[j].end <= w.start:
            j += 1
        scores, k = {}, j
        while k < len(turns) and turns[k].start < w.end:
            t = turns[k]
            ov = min(w.end, t.end) - max(w.start, t.start)
            if ov > 0:
                scores[t.speaker] = scores.get(t.speaker, 0.0) + ov
            k += 1
        if scores:
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            w.speaker = ranked[0][0]
            w.overlap = len(ranked) > 1 and ranked[1][1] > 0.3 * max(w.end - w.start, 1e-3)
        else:
            w.speaker = _nearest_speaker(turns, w, max_gap) or prev
        prev = w.speaker


def smooth(words, min_words=2, min_sec=0.6, max_gap=0.35):
    """A A [B] A A → A, если вставка B короткая, без наложения и внутри сплошной речи (паузы вокруг неё короче
    max_gap): это ошибка границы. Короткая реплика с паузами вокруг («Да.») — настоящая и остаётся."""
    runs = []
    for i, w in enumerate(words):
        if runs and runs[-1][0] == w.speaker:
            runs[-1][2] = i
        else:
            runs.append([w.speaker, i, i])
    for r in range(1, len(runs) - 1):
        spk, i0, i1 = runs[r]
        left, right = runs[r - 1][0], runs[r + 1][0]
        island = words[i0:i1 + 1]
        before = island[0].start - words[i0 - 1].end
        after = words[i1 + 1].start - island[-1].end
        if (left == right != spk and (len(island) < min_words or island[-1].end - island[0].start < min_sec)
                and not any(x.overlap for x in island) and before < max_gap and after < max_gap):
            for x in island:
                x.speaker = left
            runs[r][0] = left


def group(words, max_pause=1.5):
    """Реплики: смена говорящего или пауза длиннее max_pause."""
    utts = []
    for w in words:
        u = utts[-1] if utts else None
        if u and u.speaker == w.speaker and w.start - u.end <= max_pause:
            u.words.append(w)
            u.end = max(u.end, w.end)
        else:
            utts.append(Utterance(speaker=w.speaker, start=w.start, end=w.end, words=[w]))
    for u in utts:
        u.text = " ".join(x.text for x in u.words)
        u.overlap = any(x.overlap for x in u.words)
    return utts


def segment_speaker(start, end, turns):
    """Запасной путь без времени слов: кусок целиком — говорящему с наибольшим перекрытием.
    -> (speaker | None, overlap): overlap — второй говорящий занимает больше 35 % куска."""
    scores = {}
    for t in turns:
        ov = min(end, t.end) - max(start, t.start)
        if ov > 0:
            scores[t.speaker] = scores.get(t.speaker, 0.0) + ov
    if not scores:
        return None, False
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[0][0], len(ranked) > 1 and ranked[1][1] > 0.35 * max(end - start, 1e-3)
