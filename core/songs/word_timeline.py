"""A time for every word of the sung text — display only, never written to the card.

The aligner measures some words and leaves others without a time («Ты, ты
живешь[16.0] …» in a 25-second recognizer chunk that starts at 6 s). Spreading
the unmeasured words evenly over the phrase put them, and the chords with
them, over the intro and the breaks: intro chords stood over the first line
and chord changes went out of order.

Words are placed only where the stem has a voice:

* measured words keep their times (a clump of words squeezed into an
  instant is not a measurement and is treated as unmeasured);
* a run of unmeasured words between two measured ones is spread over the
  voiced time between them;
* when there is far more voice than the words need (a break with a lead
  instrument, a held note), the run stands next to one neighbour: before
  the next measured word when it starts a sentence or the song, after the
  previous one otherwise — or where another recognizer heard these words.

The whole song is one sequence: a run may cross a phrase boundary (a
recognizer chunk cut mid-sentence).
"""
import re

VOWELS = 'аеёиоуыэюяaeiouy'
DEFAULT_RATE_MS = 300          # per syllable, when the song has no measured words
SQUEEZED_MS = 70               # a measured syllable shorter than this is no measurement
BRIDGE_MS = 700                # silence inside one sung stretch (a breath)
ROOMY = 2.5                    # voiced time / needed time above which a run stands at one side


def syllables(text):
    return max(1, len(re.findall(f'[{VOWELS}]', str(text), re.I)))


def _norm(word):
    return re.sub(r'[^\w]+', '', str(word).casefold().replace('ё', 'е'))


def _measured(phrase):
    """Token index → (start, end, word) for words with a usable time, in order."""
    text = str(phrase.get('text') or '')
    tokens = list(re.finditer(r'\S+', text))
    if phrase.get('alignment_stale'):
        return tokens, {}
    by_span = {}
    for word in phrase.get('words') or []:
        a, b = word.get('start_ms'), word.get('end_ms')
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a:
            by_span[(word.get('char_start'), word.get('char_end'))] = word
    found, last_end = {}, None
    for i, token in enumerate(tokens):
        word = by_span.get((token.start(), token.end()))
        if word is None or (last_end is not None and word['start_ms'] < last_end - 40):
            continue
        found[i] = (int(word['start_ms']), int(word['end_ms']), word)
        last_end = int(word['end_ms'])
    # Words squeezed into an instant («Есть[65.6] пиво[65.6] и[65.7] гитара[65.7]»).
    order = sorted(found)
    squeezed = set()
    for k, i in enumerate(order[:-1]):
        j = order[k + 1]
        if j == i + 1 and found[j][0] - found[i][0] < SQUEEZED_MS * syllables(tokens[i].group()):
            squeezed.add(i)
    for k, i in enumerate(order[1:], 1):
        prev = order[k - 1]
        if prev in squeezed and prev == i - 1 and found[i][0] - found[prev][0] < 150:
            squeezed.add(i)
    return tokens, {i: v for i, v in found.items() if i not in squeezed}


def song_rate(phrases):
    """Typical milliseconds per sung syllable, from the measured words."""
    values = []
    for phrase in phrases:
        tokens, measured = _measured(phrase)
        for i, (a, b, _) in measured.items():
            if 80 <= b - a <= 1500:
                values.append((b - a) / syllables(tokens[i].group()))
    if len(values) < 5:
        return DEFAULT_RATE_MS
    values.sort()
    return max(180, min(450, values[len(values) // 2]))


def _voiced(intervals, a, b):
    return [(max(a, x), min(b, y)) for x, y in intervals if min(b, y) > max(a, x)]


def _stretch(parts, needed, backward):
    """The sung stretch next to an anchor: voiced parts, bridged over breaths,
    until the words fit; the whole stretch when it is not much longer."""
    parts = list(reversed(parts)) if backward else list(parts)
    taken, total = [], 0
    for x, y in parts:
        if taken:
            gap = (taken[-1][0] - y) if backward else (x - taken[-1][1])
            if gap > BRIDGE_MS and total >= needed:
                break
            if gap > 4 * BRIDGE_MS and total >= needed * 0.5:
                break
        taken.append((x, y))
        total += y - x
        if total >= needed * ROOMY:
            break
    if total > needed * ROOMY:
        # Trim the far end: the words take what they need, next to the anchor.
        extra, trimmed = total - needed * 1.5, []
        for x, y in reversed(taken):
            cut = min(extra, y - x)
            extra -= cut
            if backward:
                x += cut
            else:
                y -= cut
            if y > x:
                trimmed.append((x, y))
        taken = list(reversed(trimmed))
    return sorted(taken)


def _spread(run, parts):
    """Cues for the run's tokens over the voiced parts, by syllables."""
    weights = [syllables(t) for t in run]
    total, length = sum(weights), sum(y - x for x, y in parts)

    def at(share):
        target, passed = share * length, 0
        for x, y in parts:
            if target <= passed + (y - x):
                return x + (target - passed)
            passed += y - x
        return parts[-1][1]
    out, done = [], 0
    for weight in weights:
        out.append((int(at(done / total)), int(at((done + weight) / total))))
        done += weight
    return out


def _heard_score(words, window, heard):
    if not heard or not words:
        return 0.0
    a, b = window
    best = 0.0
    for x, y, said in heard:
        if min(b, y) - max(a, x) < 0.3 * min(b - a, y - x):
            continue
        best = max(best, sum(1 for w in words if w in said) / len(words))
    return best


def timelines(phrases, vocal, duration_ms=None):
    """``{id(phrase): [cue per token]}`` — ``start_ms``, ``end_ms``, ``measured``.

    Without a vocal map nothing is returned (the caller keeps its old estimate).
    """
    from .vocal_timing import _intervals
    intervals = _intervals(vocal)
    if intervals is None:
        return {}
    heard = []
    for x, y, text in (vocal.get('heard') or []) if isinstance(vocal, dict) else []:
        heard.append((int(x), int(y), {_norm(w) for w in str(text).split()} - {''}))
    rate = song_rate(phrases)
    # One sequence for the song: [phrase, token index, text, start, end, word].
    seq = []
    for phrase in phrases:
        tokens, measured = _measured(phrase)
        ps, pe = int(phrase.get('start_ms') or 0), int(phrase.get('end_ms') or 0)
        if not measured and tokens and pe > ps:
            # No measured word: the phrase's own span is all there is.
            parts = _voiced(intervals, ps, pe) or [(ps, pe)]
            for (a, b), (i, token) in zip(_spread([t.group() for t in tokens], parts), enumerate(tokens)):
                seq.append([phrase, i, token.group(), a, b, None, True])
            continue
        for i, token in enumerate(tokens):
            a, b, word = measured.get(i, (None, None, None))
            seq.append([phrase, i, token.group(), a, b, word, False])
    end_of_song = int(duration_ms or max([0, *[y for _, y in intervals],
                                           *[int(p.get('end_ms') or 0) for p in phrases]]))
    k = 0
    while k < len(seq):
        if seq[k][3] is not None:
            k += 1
            continue
        j = k
        while j < len(seq) and seq[j][3] is None:
            j += 1
        run = [item[2] for item in seq[k:j]]
        ta = seq[k - 1][4] if k else 0
        tb = seq[j][3] if j < len(seq) else end_of_song
        if tb <= ta:                                   # anchors out of order: stay inside the phrase
            tb = max(ta + 1, int(seq[j - 1][0].get('end_ms') or ta + 1))
        needed = sum(syllables(t) for t in run) * rate
        parts = _voiced(intervals, ta, tb)
        voiced = sum(y - x for x, y in parts)
        if not parts:
            # No voice between the neighbours: next to the following word.
            parts = [(max(ta, tb - needed), tb)] if j < len(seq) else [(ta, min(tb, ta + needed))]
        elif voiced > needed * ROOMY:
            before = _stretch(parts, needed, backward=True) if j < len(seq) else None
            after = _stretch(parts, needed, backward=False) if k else None
            if before and after:
                words = [w for w in (_norm(t) for t in run) if w]
                span = lambda p: (p[0][0], p[-1][1])
                sb, sa = _heard_score(words, span(before), heard), _heard_score(words, span(after), heard)
                if abs(sb - sa) >= 0.25 and max(sb, sa) >= 0.5:
                    parts = before if sb > sa else after
                else:
                    # A new sentence (or line) belongs to what follows it.
                    previous = seq[k - 1][2]
                    starts_sentence = bool(re.search(r'[.!?…]["»”)]*$', previous)) or run[0][:1].isupper()
                    parts = before if starts_sentence else after
            else:
                parts = before or after
        for item, (a, b) in zip(seq[k:j], _spread(run, parts)):
            item[3], item[4] = a, max(a + 1, b)
        k = j
    out, spans = {}, {}
    for phrase, i, text, a, b, word, _ in seq:
        if id(phrase) not in spans:
            spans[id(phrase)] = [(m.start(), m.end()) for m in re.finditer(r'\S+', str(phrase.get('text') or ''))]
        cs, ce = spans[id(phrase)][i]
        out.setdefault(id(phrase), []).append({'char_start': cs, 'char_end': ce, 'start_ms': a, 'end_ms': b,
                                               'measured': word is not None, 'method': (word or {}).get('method')})
    return out
