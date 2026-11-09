"""Presentation-only lyric lines; preserve original phrases and manual history.

Lines follow the verse, not the recognizer's segments. A recognizer cuts a
sung line wherever the singer breathes («Я молча | пил, а лес валил»), so a
short pause inside a verse no longer ends a line. Verse lines are chosen
over the whole passage at once: a sung line has a steady length in
syllables (the meter of the song), it ends where the next word starts with
a capital letter, at punctuation or at a pause, and not after a preposition
or conjunction. Lines are then grouped into stanzas — four lines where the
song allows it, split at the longest pauses, never across a break or a part
of the song (verse, chorus).
"""
import re
from .playback_layout import display_words

VOWELS = set('аеёиоуыэюяaeiouy')
FUNCTION_WORDS = set('не ни и а но в во на с со к ко по без для о об обо из от у за под над при про '
                     'что как я ты он она мы вы они же бы ли то да'.split())
# Weights of the verse-line cues (measured on 12 songs with published
# lyrics: line breaks F1 0.73 → 0.84 against the previous heuristics).
CUE = dict(cap=3.0, strong=2.4, weak=1.5, gap=1.5, phrase=0.3, func=2.8, meter=0.8)
# Hard limit of one verse line; longer lines wrap as continuation lines.
VERSE_CHARS = 64
# A short gap between two lyric rows stays inside the verse.
VERSE_PAUSE_MS = 2500
STANZA_SIZE_COST = {1: 3.0, 2: 1.2, 3: 1.4, 4: 0.0, 5: 1.6, 6: 1.4, 7: 3.0, 8: 2.5}


def syllables(text):
    return max(1, sum(c in VOWELS for c in str(text).lower()))


def _norm(word):
    return re.sub(r'[^\w]+', '', word.casefold().replace('ё', 'е'))


def _cue_values(prev, nxt, gap, phrase_change):
    """Break preference between two words (higher: more likely a line end)."""
    cap = nxt[:1].isupper()
    strong = bool(re.search(r'[.!?…]["»”)]*$', prev))
    weak = bool(re.search(r'[,;:—–-]["»”)]*$', prev))
    pause = min(1.6, gap / 600.0) if gap is not None and gap >= 120 else 0.0
    return (CUE['cap'] * cap + CUE['strong'] * strong + CUE['weak'] * weak + CUE['gap'] * pause
            + CUE['phrase'] * phrase_change - CUE['func'] * (_norm(prev) in FUNCTION_WORDS))


def song_meter(tokens):
    """Typical syllables per sung line: the passages between clear line ends.

    ``tokens``: dicts with ``text`` and ``bonus`` (break cue after the word).
    """
    lengths, count = [], 0
    for token in tokens:
        count += syllables(token['text'])
        if token.get('clear_end'):
            lengths.append(count)
            count = 0
    lengths = sorted(x for x in lengths if 4 <= x <= 20)
    return lengths[int(len(lengths) * .6)] if lengths else 10


def verse_cuts(tokens, meter, hard=(), max_chars=VERSE_CHARS):
    """Indexes ``i`` after which a verse line ends (dynamic programming).

    Cost of a line: deviation from the song's meter; reward of a cut: its cue.
    ``hard``: cuts that must be made (explicit line breaks).
    """
    n = len(tokens)
    if n <= 1:
        return []
    syl = [0]
    chars = [0]
    for t in tokens:
        syl.append(syl[-1] + syllables(t['text']))
        chars.append(chars[-1] + len(t['text']) + 1)
    hard = sorted(set(hard))
    best = [float('inf')] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            if chars[j] - chars[i] > max_chars and j - i > 1:
                break
            if any(i <= h < j - 1 for h in hard):
                break
            k = syl[j] - syl[i]
            cost = CUE['meter'] * (k - meter) ** 2 / meter
            reward = tokens[j - 1]['bonus'] if j < n else 0.0
            total = best[i] + cost - reward
            if total < best[j]:
                best[j], back[j] = total, i
    cuts, j = [], n
    while j > 0:
        i = back[j]
        if i > 0:
            cuts.append(i - 1)
        j = i
    return sorted(cuts)


def stanza_starts(gaps):
    """Indexes of lines (after the first) that begin a stanza.

    ``gaps[i]``: pause in ms after line ``i`` (``None`` unknown). Four lines
    per stanza where possible; other sizes cost more, a long pause pays.
    """
    n = len(gaps) + 1
    best = [float('inf')] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for size in range(1, min(8, j) + 1):
            i = j - size
            pay = min(2.0, max(0.0, gaps[j - 1] or 0) / 1000.0) if j < n else 0.0
            total = best[i] + STANZA_SIZE_COST[size] - pay
            if total < best[j]:
                best[j], back[j] = total, i
    starts, j = [], n
    while j > 0:
        i = back[j]
        if i > 0:
            starts.append(i)
        j = i
    return sorted(starts)


def _balance_soft_wraps(text, bounds, soft, max_chars):
    """Avoid one/two-word tails created by width, keeping semantic breaks intact."""
    for j in range(1, len(bounds)-1):
        left, cut, right = bounds[j-1:j+2]
        if cut not in soft or len(text[cut:right].split()) > 2:
            continue
        words = list(re.finditer(r'\S+', text[left:right]))
        candidates = []
        for i in range(3, len(words)-2):
            at = left + words[i].start()
            if at >= cut or len(text[left:at].rstrip()) > max_chars or len(text[at:right].rstrip()) > max_chars:
                continue
            previous = words[i-1].group()
            if previous.casefold() in {'не','ни','и','а','но','в','на','с','к','по','без','для','о','из','от'}:
                continue
            candidates.append((previous.endswith(','), at))
        if candidates:
            bounds[j] = max(candidates)[1]
    return bounds


def _sounding(events, t):
    """The chord already sounding at ``t`` (not a new change)."""
    active = None
    for event in events or []:
        start = event.get('start_ms')
        if not isinstance(start, (int, float)) or start > t + 150:
            continue
        end = event.get('end_ms')
        if isinstance(end, (int, float)) and end <= t + 50:
            continue
        if active is None or start >= active['start_ms']:
            active = event
    return active if active and active.get('name') not in (None, '', 'N') else None


def _tokens_of(text, spans, cues):
    """Words of a passage with the cue for a line end after each of them."""
    words = list(re.finditer(r'\S+', text))
    timed = {w['char_start']: w for w in cues if w.get('quality') == 'aligned'}
    owner = []
    for m in words:
        owner.append(next((k for k, (lo, hi, _) in enumerate(spans) if lo <= m.start() < hi), None))
    out = []
    for k, m in enumerate(words):
        token = {'text': m.group(), 'start': m.start(), 'end': m.end(), 'bonus': 0.0, 'hard': False}
        if k + 1 < len(words):
            nxt = words[k + 1]
            gap = None
            a, b = timed.get(m.start()), timed.get(nxt.start())
            if a and b:
                gap = b['start_ms'] - a['end_ms']
            elif owner[k] != owner[k + 1] and owner[k] is not None and owner[k + 1] is not None:
                gap = spans[owner[k + 1]][2].get('start_ms', 0) - spans[owner[k]][2].get('end_ms', 0)
            token['hard'] = '\n' in text[m.end():nxt.start()]
            token['bonus'] = _cue_values(m.group(), nxt.group(), gap, owner[k] != owner[k + 1])
            token['clear_end'] = nxt.group()[:1].isupper() and (
                bool(re.search(r'[.!?…,;:—–-]["»”)]*$', m.group())) or (gap or 0) >= 300)
        out.append(token)
    return out


def readable_sheet(rows, max_chars=42, section_starts=(), events=None):
    output, group, waiting, absorbed = [], [], [], []
    starts, opened = set(section_starts), set()
    seen_changes = set()
    # One meter for the whole song, from every lyric row.
    passage = [t for row in rows if row.get('text')
               for t in _tokens_of(row['text'], [(0, len(row['text']), row)], display_words(row))]
    meter = song_meter(passage)

    def flush():
        if not group:
            return
        text, marks, spans, cues = '', [], [], []
        for row, carried in group:
            if text and not text[-1].isspace():
                text += ' '
            offset = len(text)
            text += row['text']
            spans.append((offset, len(text), row))
            cues.extend(dict(w, phrase_id=row.get('phrase_id'),
                             phrase_char_start=row.get('source_char_offset', 0)+w['char_start'],
                             char_start=offset+w['char_start'], char_end=offset+w['char_end'])
                        for w in display_words(row))
            # The full sheet already shows the original change (including intro/gap rows).
            # Carry-in marks belong to isolated phrase previews, not new changes.
            for m in [*carried, *row.get('chords', [])]:
                key = (m.get('start_ms'), m.get('name'))
                if m.get('char_index') is not None and key not in seen_changes:
                    marks.append(dict(m, char_index=offset+m['char_index']))
                    seen_changes.add(key)
        tokens = _tokens_of(text, spans, cues)
        cuts = verse_cuts(tokens, meter, [k for k, t in enumerate(tokens) if t['hard']])
        verses, first = [], 0
        for cut in [*cuts, len(tokens) - 1]:
            verses.append(tokens[first:cut + 1])
            first = cut + 1
        pieces = []
        for verse in verses:
            if not verse:
                continue
            if verse[-1]['end'] - verse[0]['start'] <= max_chars or len(verse) == 1:
                pieces.append((verse[0]['start'], False))
                continue
            # A verse wider than the sheet wraps at its best inner point.
            parts = -(-(verse[-1]['end'] - verse[0]['start']) // max_chars)
            inner = verse_cuts(verse, max(3, sum(syllables(t['text']) for t in verse) / parts), max_chars=max_chars)
            pieces.append((verse[0]['start'], False))
            pieces.extend((verse[k + 1]['start'], True) for k in inner)
        bounds = [a for a, _ in pieces] + [len(text)]
        continuation = {a: c for a, c in pieces}
        shown = set()
        for a, b in zip(bounds, bounds[1:]):
            content = text[a:b].rstrip()
            if not content:
                continue
            owners = [r for lo, hi, r in spans if lo < b and hi > a]
            source = owners[0]
            source_spans = []
            for lo, hi, owner in spans:
                left, right = max(lo, a), min(hi, a + len(content))
                if left >= right:
                    continue
                source_spans.append({
                    'phrase_id': owner.get('phrase_id'),
                    'phrase_char_start': owner.get('source_char_offset', 0) + left - lo,
                    'phrase_char_end': owner.get('source_char_offset', 0) + right - lo,
                    'display_char_start': left - a,
                    'display_char_end': right - a,
                })
            chosen = [dict(m, char_index=m['char_index']-a) for m in marks if a <= m['char_index'] < a+len(content)]
            line_cues = [w for w in cues if a <= w['char_start'] and w['char_end'] <= a+len(content)]
            if events is not None and not any(m['char_index'] == 0 for m in chosen):
                # Every sung line shows its chord: the one sounding when the
                # line starts, repeated from above if it has not changed.
                t = min((w['start_ms'] for w in line_cues), default=source.get('start_ms', 0))
                active = _sounding(events, t)
                first_mark = min((m for m in chosen), key=lambda m: m['char_index'], default=None)
                if active and not (first_mark and abs(first_mark.get('start_ms', -10**9) - t) <= 300):
                    chosen.insert(0, dict(active, char_index=0, anchor_quality='continuation', continued=True))
            grouped = {}
            for m in chosen:
                grouped.setdefault(m['char_index'], []).append(m['name'])
            positions = sorted({0, *grouped})
            cells = [{'char_index': pos, 'text': content[pos:positions[j+1] if j+1 < len(positions) else len(content)],
                      'chords': list(dict.fromkeys(grouped.get(pos, [])))} for j, pos in enumerate(positions)]
            pid = source.get('phrase_id')
            display_id = pid if pid not in shown and not source.get('source_char_offset') else f"{pid}:line:{source.get('source_char_offset', 0)+a}"
            shown.add(pid)
            output.append({**source, 'phrase_id': display_id, 'text': content, 'cells': cells, 'chords': chosen,
                'unplaced_chords': [], 'display_only': True, 'source_phrase_ids': [r.get('phrase_id') for r in owners],
                'source_spans': source_spans, 'verse_continuation': continuation.get(a, False),
                'section_start': a == 0 and group[0][0].get('phrase_id') in starts and group[0][0].get('source_char_offset', 0) == 0,
                'display_words': [dict(w, char_start=w['char_start']-a, char_end=w['char_end']-a)
                                  for w in line_cues]})
        group.clear()
        # Short pauses folded into the verse stay in the data for timing,
        # without chords (they are shown over the next word) and marked.
        output.extend(absorbed)
        absorbed.clear()

    def close():
        flush()
        for gap in waiting:
            output.append(gap)
            seen_changes.update((c.get('start_ms'), c.get('name')) for c in gap.get('unplaced_chords', []))
        waiting.clear()

    for row in rows:
        if not row.get('text'):
            short = (not row.get('vocal') and int(row.get('end_ms') or 0) - int(row.get('start_ms') or 0) <= VERSE_PAUSE_MS)
            if group and short and not any(g.get('vocal') for g in waiting):
                waiting.append(row)
                continue
            close()
            output.append(row)
            seen_changes.update((c.get('start_ms'), c.get('name')) for c in row.get('unplaced_chords', []))
            continue
        pause = sum(int(g.get('end_ms') or 0) - int(g.get('start_ms') or 0) for g in waiting)
        # A part of the song (verse, chorus) begins at its first phrase only;
        # the following slices of that phrase continue the same verse.
        opens = row.get('phrase_id') in starts and row.get('phrase_id') not in opened
        opened.add(row.get('phrase_id'))
        if group and (row.get('start_ms', 0)-group[-1][0].get('end_ms', 0) > VERSE_PAUSE_MS
                      or opens or pause > VERSE_PAUSE_MS):
            close()
        carried = []
        for gap in waiting:
            # Chord changes during a breath inside the verse go over the
            # next word, where the singer continues.
            carried.extend(dict(c, char_index=0) for c in gap.get('unplaced_chords', []) if c.get('name') not in (None, '', 'N'))
            absorbed.append(dict(gap, unplaced_chords=[], pause=True, verse_pause=True))
        waiting.clear()
        group.append((row, carried))
    close()
    _mark_stanzas(output)
    return output


def _line_span(line):
    words = [w for w in line.get('display_words') or [] if isinstance(w.get('start_ms'), (int, float))]
    if words:
        return min(w['start_ms'] for w in words), max(w['end_ms'] for w in words)
    return line.get('start_ms'), line.get('end_ms')


def _mark_stanzas(output):
    """``stanza_start`` on the first line of every stanza (see stanza_starts)."""
    runs, current = [], []
    for k, line in enumerate(output):
        if not line.get('text'):
            if line.get('verse_pause'):
                continue
            if current:
                runs.append(current)
            current = []
            continue
        if line.get('verse_continuation'):
            continue
        if line.get('section_start') and current:
            runs.append(current)
            current = []
        current.append(k)
    if current:
        runs.append(current)
    for run in runs:
        ends = {}
        for k in run:
            ends[k] = _line_span(output[k])[1]
        # The verse line may continue on wrapped lines: its end is the last one.
        for k in run:
            j = k + 1
            while j < len(output) and output[j].get('text') and output[j].get('verse_continuation'):
                ends[k] = _line_span(output[j])[1]
                j += 1
        gaps = []
        for a, b in zip(run, run[1:]):
            start, end = _line_span(output[b])[0], ends[a]
            gaps.append(start - end if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None)
        output[run[0]]['stanza_start'] = True
        for i in stanza_starts(gaps):
            output[run[i]]['stanza_start'] = True
