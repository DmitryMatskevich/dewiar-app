"""Where somebody sings: vocal activity from the separated vocal stem.

Lexical evidence (ASR words, forced alignment) says WHAT was sung. Its edges
are unreliable: an aligner stretches the first word back to the crop start
(«То» from 0 to 13 s), keeps the last word through a musical break, and an ASR
segment may begin at 0 s although the vocal enters after a long intro.

The vocal stem says WHEN a voice sounds. This module turns it into intervals
and uses them for two deterministic, text-preserving operations:

* ``fit_phrases`` — move phrase/word edges out of stretches without voice;
  text, ids, manual flags and the lexical order are never changed;
* ``split_gap`` — a gap between lyric rows is a break; a short one is only a
  pause between lines; a voiced stretch for which a recognizer heard words
  that the selected text lacks is singing without recognized text, not
  «Проигрыш».

What the separated stem can and cannot say (measured on 36 library songs):
silence in the stem is reliable — no voice there. Energy is not: a lead
accordion, violin or saxophone lands in the same stem, so a solo looks
«voiced». Hence edges move only out of silence, and a voiced gap is called
singing only with lexical support from an independent recognizer.

Only numpy is required, so the same code runs in the worker, in the app core
and in the library retiming tool. A stem without a clear voice/no-voice
contrast yields ``None``; callers then keep the previous behaviour.
"""
from __future__ import annotations

import copy
import math
import re

VERSION = 2
HOP_MS = 20
WINDOW_MS = 40

# A pause shorter than this is a breath, consonant or staccato, not a break.
BRIDGE_MS = 450
# Shorter voiced islands are leakage clicks.
MIN_VOICE_MS = 220
# Unvoiced stretch at a phrase/word edge which is treated as instrumental.
EDGE_MS = 1000
# A voiced stretch inside a lyric gap of at least this length is unrecognized
# singing (or speech), never an instrumental break. Shorter voice between two
# lines is a word tail or a breath and stays a plain pause.
GAP_VOICE_MS = 2500
# Unvoiced gap between lines shorter than this is a pause, not «Проигрыш».
BREAK_MS = 3000


# ---------------------------------------------------------------- features

def envelope(samples, sr):
    """Frame features of a mono signal: rms, voice band (250–3500 Hz), flatness."""
    import numpy as np
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    hop = max(1, round(sr * HOP_MS / 1000))
    win = max(hop, round(sr * WINDOW_MS / 1000))
    if len(x) < win:
        return {'hop_ms': HOP_MS, 'rms': [], 'voice_band': [], 'flatness': []}
    frames = 1 + (len(x) - win) // hop
    result = {'hop_ms': HOP_MS, 'rms': [], 'voice_band': [], 'flatness': []}
    freqs = np.fft.rfftfreq(win, 1 / sr)
    voice = (freqs >= 250) & (freqs <= 3500)
    taper = np.hanning(win).astype(np.float32)
    # Blocks keep memory bounded for long recordings.
    for first in range(0, frames, 2048):
        count = min(2048, frames - first)
        index = np.arange(win)[None, :] + hop * (first + np.arange(count))[:, None]
        blocks = x[index] * taper[None, :]
        power = np.abs(np.fft.rfft(blocks, axis=1)) ** 2 + 1e-12
        result['rms'].extend(np.sqrt(np.mean(blocks ** 2, axis=1)).tolist())
        result['voice_band'].extend((np.sqrt(power[:, voice].sum(axis=1)) / win).tolist())
        band = power[:, voice]
        result['flatness'].extend((np.exp(np.mean(np.log(band), axis=1)) / np.mean(band, axis=1)).tolist())
    return result


def _frames(env):
    import numpy as np
    band = np.asarray(env.get('voice_band') or [], dtype=np.float64)
    flat = np.asarray(env.get('flatness') or [1.0] * len(band), dtype=np.float64)
    return band, flat


def activity(env, duration_ms=None):
    """Vocal intervals ``[[start_ms, end_ms], ...]`` or ``None`` if unreliable."""
    import numpy as np
    band, flat = _frames(env)
    hop = int(env.get('hop_ms') or HOP_MS)
    if len(band) < 50:
        return None
    db = 20 * np.log10(band + 1e-9)
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 97))
    spread = peak - floor
    # Without a clear contrast the stem cannot separate voice from leakage.
    if spread < 14:
        return None
    on = floor + .42 * spread
    off = floor + .30 * spread
    loud = floor + .62 * spread
    # Harmonic frames (voice) are spectrally peaky; leakage of drums and
    # noise is flat. Very loud frames count regardless of flatness.
    tonal = (flat < .45) | (db >= loud)
    voiced = np.zeros(len(db), dtype=bool)
    state = False
    for i, value in enumerate(db):
        if state:
            state = value >= off
        else:
            state = value >= on and bool(tonal[i])
        voiced[i] = state
    intervals = []
    start = None
    for i, value in enumerate(voiced):
        if value and start is None:
            start = i
        elif not value and start is not None:
            intervals.append([start * hop, i * hop + WINDOW_MS // 2])
            start = None
    if start is not None:
        intervals.append([start * hop, len(voiced) * hop + WINDOW_MS // 2])
    merged = []
    for a, b in intervals:
        if merged and a - merged[-1][1] < BRIDGE_MS:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    result = [[int(a), int(b)] for a, b in merged if b - a >= MIN_VOICE_MS]
    # Anything above a low threshold, tonal or not: used only to decide that
    # a lyric line has no voice at all, so quiet singing is not lost.
    weak = _runs(db >= floor + .25 * spread, hop, 100)
    if duration_ms:
        result = [[a, min(b, int(duration_ms))] for a, b in result if a < duration_ms]
        weak = [[a, min(b, int(duration_ms))] for a, b in weak if a < duration_ms]
    return {'version': VERSION, 'hop_ms': hop, 'intervals': result, 'weak': weak,
            'floor_db': round(floor, 1), 'peak_db': round(peak, 1)}


def _runs(mask, hop, min_ms):
    out, start = [], None
    for i, value in enumerate(list(mask) + [False]):
        if value and start is None:
            start = i
        elif not value and start is not None:
            a, b = start * hop, i * hop + WINDOW_MS // 2
            if out and a - out[-1][1] < BRIDGE_MS:
                out[-1][1] = b
            else:
                out.append([a, b])
            start = None
    return [[int(a), int(b)] for a, b in out if b - a >= min_ms]


# ---------------------------------------------------------------- queries

def _intervals(vocal):
    if not vocal:
        return None
    rows = vocal.get('intervals') if isinstance(vocal, dict) else vocal
    if not isinstance(rows, list):
        return None
    return [(int(a), int(b)) for a, b in rows if b > a]


def voiced_ms(vocal, a, b):
    rows = _intervals(vocal) or []
    return sum(max(0, min(b, y) - max(a, x)) for x, y in rows)


def first_voice(vocal, a, b):
    """First voiced moment inside [a, b) or None."""
    for x, y in _intervals(vocal) or []:
        if y > a and x < b:
            return max(a, x)
    return None


def last_voice(vocal, a, b):
    """End of the last voiced stretch inside [a, b) or None."""
    found = None
    for x, y in _intervals(vocal) or []:
        if y > a and x < b:
            found = min(b, y)
    return found


def split_gap(vocal, a, b, song_end=None):
    """Split a gap between lyric rows into ``(start, end, kind)`` parts.

    ``kind`` is ``'vocal'`` (singing without recognized text), ``'break'``
    (Вступление/Проигрыш/Окончание) or ``'pause'`` (between two lines; shows
    its chord changes without a label). Without a vocal map the whole gap is a
    ``'break'`` as before.

    * singing: voice in the stem (≥ 1.5 s in a cluster) where an independent
      recognizer heard words the text lacks (``vocal['lexical']``);
    * break: a silent stem for ≥ 3 s, the song's start/end, or ≥ 3 s of a
      «voiced» stem without such words — a lead instrument, not a singer;
    * pause: everything shorter — a breath between lines, or a line whose
      words the aligner placed late.
    """
    rows = _intervals(vocal)
    if rows is None or b <= a:
        return [(a, b, 'break')]
    lexical = [(int(x), int(y)) for x, y in (vocal.get('lexical') or []) if y > x] if isinstance(vocal, dict) else []
    clusters = []
    for x, y in rows:
        x, y = max(a, x), min(b, y)
        if y <= x:
            continue
        if clusters and x - clusters[-1][1] < 1200:
            clusters[-1][1] = y
            clusters[-1][2] += y - x
        else:
            clusters.append([x, y, y - x])
    passages = []
    for x, y, voiced in clusters:
        if any(x < q and y > p for p, q in lexical):
            # Lines of one unrecognized passage, separated by breaths.
            if passages and x - passages[-1][1] < BREAK_MS:
                passages[-1][1] = y
                passages[-1][2] += voiced
            else:
                passages.append([x, y, voiced])
    sung = [(x, y) for x, y, voiced in passages if voiced >= GAP_VOICE_MS]

    def plain(x, y):
        inside = [(max(x, p), min(y, q)) for p, q in rows if min(y, q) > max(x, p)]
        voiced = sum(q - p for p, q in inside)
        silent, cursor = 0, x
        for p, q in inside:
            silent = max(silent, p - cursor)
            cursor = max(cursor, q)
        silent = max(silent, y - cursor)
        edge = (x == 0 or (song_end is not None and y >= song_end - 50)) and y - x >= 1000
        solo = y - x >= BREAK_MS and voiced >= .6 * (y - x)
        return (x, y, 'break' if silent >= BREAK_MS or edge or solo else 'pause')

    out, cursor = [], a
    for x, y in sung:
        if x - cursor >= 300:
            out.append(plain(cursor, x))
        else:
            x = cursor
        out.append((x, y, 'vocal'))
        cursor = y
    if b - cursor >= 300 or not out:
        out.append(plain(cursor, b))
    else:
        x, _, kind = out.pop()
        out.append((x, b, kind))
    return out


def unrecognized_windows(phrases, hypotheses):
    """Windows where a recognizer heard at least three words the text lacks.

    Chunk-level hypotheses (Qwen 25-second windows) count: their position is
    coarse, but the vocal map localizes the singing inside the window.
    """
    from difflib import SequenceMatcher

    def tokens(text):
        return [t for t in (re.sub(r'[^\w]+', '', w.casefold().replace('ё', 'е'))
                            for w in str(text or '').split()) if t]
    rows = [p for p in phrases or [] if str(p.get('text') or '').strip()
            and p.get('vocal_evidence') != 'none']
    out = []
    for hypothesis in hypotheses or []:
        for candidate in hypothesis.get('phrases') or []:
            a, b = candidate.get('start_ms'), candidate.get('end_ms')
            if not (finite(a) and finite(b)) or b <= a:
                continue
            heard = tokens(candidate.get('text'))
            if len(heard) < 3:
                continue
            near = [t for p in rows if int(p.get('start_ms') or 0) < b + 2000
                    and int(p.get('end_ms') or 0) > a - 2000 for t in tokens(p.get('text'))]
            matched = sum(block.size for block in
                          SequenceMatcher(None, heard, near, autojunk=False).get_matching_blocks())
            # Spelling variants leave a few unmatched words in every window;
            # a missing line leaves a large share of them.
            missing = len(heard) - matched
            if missing >= 3 and missing >= .4 * len(heard):
                out.append([int(a), int(b)])
    return out


# ---------------------------------------------------------------- fitting

def _measured(phrase):
    if phrase.get('alignment_stale'):
        return []
    text = str(phrase.get('text') or '')
    return sorted([w for w in phrase.get('words') or []
                   if isinstance(w.get('start_ms'), (int, float)) and isinstance(w.get('end_ms'), (int, float))
                   and w['end_ms'] > w['start_ms'] and isinstance(w.get('char_start'), int)
                   and 0 <= w['char_start'] < max(1, len(text))], key=lambda w: w['char_start'])


def _fit_word(word, vocal, is_first, is_last, previous_end, next_start, notes):
    a, b = int(word['start_ms']), int(word['end_ms'])
    duration = b - a
    if duration < 1200 or str(word.get('method') or '').startswith('manual'):
        return word
    voice_a = first_voice(vocal, a, b)
    voice_b = last_voice(vocal, a, b)
    changed = dict(word)
    if voice_a is None:
        # A long word with no voice at all: the aligner put it into a break.
        # Only an edge word can be moved — to the voice next to its neighbour.
        if is_first and next_start is not None:
            onset = first_voice(vocal, b, next_start + 1)
            if onset is not None and next_start - onset >= 120:
                changed.update(start_ms=onset, end_ms=min(next_start, onset + min(duration, 1500)))
        elif is_last and previous_end is not None:
            tail = last_voice(vocal, previous_end, a)
            if tail is not None and tail - previous_end >= 120:
                changed.update(start_ms=max(previous_end, tail - min(duration, 1500)), end_ms=tail)
        if changed is not word and (changed['start_ms'], changed['end_ms']) != (a, b):
            changed['timing_fit'] = 'vocal_stem_relocated'
            changed['measured_start_ms'], changed['measured_end_ms'] = a, b
            notes.append(dict(text=word.get('text'), char_start=word.get('char_start'),
                              reason='word_outside_voice', before=[a, b],
                              after=[changed['start_ms'], changed['end_ms']]))
            return changed
        changed['vocal_evidence'] = 'none'
        return changed
    start, end = a, b
    if voice_a - a >= EDGE_MS:
        start = max(a, voice_a - 60)
    if b - voice_b >= EDGE_MS + 500:
        end = min(b, voice_b + 150)
    if end - start < 80 or (start, end) == (a, b):
        return word
    changed.update(start_ms=start, end_ms=end, timing_fit='vocal_stem_edges',
                   measured_start_ms=a, measured_end_ms=b)
    notes.append(dict(text=word.get('text'), char_start=word.get('char_start'),
                      reason='stretched_over_break', before=[a, b], after=[start, end]))
    return changed


HELD_MS = 3000


def _held_end(vocal, end, next_start=None, notes=None):
    """A line whose last word is not measured ends with the voice, not before it.

    «Как сто балерин»: the aligner timed «Как сто», the line stopped there and
    «балерин» (still sung, 1.5 s) fell into the pause, which then showed its
    chord on a line of its own. The line keeps the voiced stretch it ends in,
    up to the next line and at most ``HELD_MS``.
    """
    for x, y in _intervals(vocal) or []:
        if x <= end < y:
            target = min(y + 150, end + HELD_MS)
            if next_start is not None:
                target = min(target, int(next_start))
            if target > end:
                if notes is not None:
                    notes.append(dict(reason='held_last_word', before=end, after=target))
                return target
            break
    return end


def fit_phrase(phrase, vocal, previous_end=None, next_start=None):
    """Return ``(phrase, notes)`` with edges moved out of unvoiced stretches."""
    rows = _intervals(vocal)
    out = copy.deepcopy(phrase)
    notes = []
    if rows is None or not str(out.get('text') or '').strip():
        return out, notes
    # A boundary the user chose while listening is stronger evidence than
    # the stem; so is a word the user timed by hand.
    if out.get('timing_quality') == 'manual_phrase' or out.get('timing_locked'):
        return out, notes
    start, end = int(out.get('start_ms') or 0), int(out.get('end_ms') or 0)
    if end <= start:
        return out, notes
    words = _measured(out)
    tokens = list(re.finditer(r'\S+', str(out.get('text') or '')))
    if any(int(w['start_ms']) < start - 500 or int(w['end_ms']) > end + 500 for w in words):
        # Word times outside their own phrase: the card is inconsistent
        # (overlapping recognitions). Moving edges would only collapse it.
        notes.append(dict(reason='inconsistent_word_times', before=[start, end]))
        out['timing_fit'] = 'skipped_inconsistent'
        return out, notes
    span = (int(words[0]['start_ms']), int(words[-1]['end_ms'])) if words else (start, end)
    weak = vocal.get('weak') if isinstance(vocal, dict) and vocal.get('weak') is not None else rows
    if span[1] - span[0] >= 800 and voiced_ms(weak, *span) < .1 * (span[1] - span[0]):
        # Text where the stem is silent: a recognizer's invention over the
        # music (credits, names) or a line placed into a break. Keep it for
        # review; the sheet shows it apart from the sung lines.
        if out.get('vocal_evidence') != 'none':
            notes.append(dict(reason='text_without_voice', before=list(span)))
        out['vocal_evidence'] = 'none'
        return out, notes
    out.pop('vocal_evidence', None)
    if words:
        fitted = []
        for i, word in enumerate(words):
            prev_end = fitted[-1]['end_ms'] if fitted else None
            nxt = words[i + 1]['start_ms'] if i + 1 < len(words) else None
            fitted.append(_fit_word(word, vocal, i == 0, i + 1 == len(words), prev_end, nxt, notes))
        by_start = {w['char_start']: w for w in fitted}
        out['words'] = [by_start.get(w.get('char_start'), w) if w in words else w
                        for w in out.get('words') or []]
        first, last = fitted[0], fitted[-1]
        covers_start = first['char_start'] == (tokens[0].start() if tokens else 0)
        covers_end = bool(tokens) and last.get('char_end') == tokens[-1].end()
        if covers_start:
            start = int(first['start_ms'])
        else:
            # Unmeasured opening words are sung before the first measured
            # one; only an unvoiced lead from the phrase edge is removed.
            onset = first_voice(vocal, start, int(first['start_ms']) + 1)
            if onset is not None and onset - start >= EDGE_MS:
                start = onset - 60
        if covers_end:
            end = int(last['end_ms'])
        else:
            tail = last_voice(vocal, int(last['end_ms']), end)
            if tail is not None and end - tail >= EDGE_MS + 500:
                end = tail + 150
            elif tail is None and end - int(last['end_ms']) >= EDGE_MS + 500:
                end = int(last['end_ms'])
            end = _held_end(vocal, end, next_start, notes)
    else:
        onset = first_voice(vocal, start, end)
        tail = last_voice(vocal, start, end)
        if onset is None:
            if end - start >= 1000:
                out['vocal_evidence'] = 'none'
                notes.append(dict(reason='phrase_without_voice', before=[start, end]))
            return out, notes
        if onset - start >= EDGE_MS:
            start = onset - 60
        if end - tail >= EDGE_MS + 500:
            end = tail + 150
        end = _held_end(vocal, end, next_start, notes)
    if previous_end is not None:
        start = max(start, int(previous_end))
    start = max(0, start)
    if end - start < 200:
        # Never collapse a line; an overlap with the previous line is a text
        # problem for review, not something an edge move can solve.
        return phrase_copy_without_fit(phrase, notes)
    if (start, end) != (int(out['start_ms']), int(out['end_ms'])):
        out.setdefault('measured_start_ms', int(out['start_ms']))
        out.setdefault('measured_end_ms', int(out['end_ms']))
        notes.append(dict(reason='phrase_edges', phrase_id=out.get('phrase_id'),
                          before=[int(out['start_ms']), int(out['end_ms'])], after=[start, end]))
        out['start_ms'], out['end_ms'] = start, end
        out['timing_fit'] = 'vocal_stem'
    return out, notes


def phrase_copy_without_fit(phrase, notes):
    out = copy.deepcopy(phrase)
    out['timing_fit'] = 'skipped_overlap'
    del notes[:]
    notes.append(dict(reason='skipped_overlap', before=[int(phrase.get('start_ms') or 0),
                                                        int(phrase.get('end_ms') or 0)]))
    return out, notes


def fit_phrases(phrases, vocal):
    """Fit every phrase; never reorder, merge, drop or rewrite text."""
    if _intervals(vocal) is None:
        return copy.deepcopy(phrases or []), {'applied': False, 'reason': 'no_vocal_activity', 'version': VERSION}
    out, report = [], []
    for i, phrase in enumerate(phrases or []):
        nxt = phrases[i + 1].get('start_ms') if i + 1 < len(phrases) else None
        fitted, notes = fit_phrase(phrase, vocal, out[-1]['end_ms'] if out else None, nxt)
        out.append(fitted)
        changes = [n for n in notes if n['reason'] not in SKIPPED]
        skipped = [n for n in notes if n['reason'] in SKIPPED]
        if changes or skipped:
            report.append({'phrase_id': phrase.get('phrase_id'), 'text': str(phrase.get('text') or '')[:80],
                           'changes': changes, 'skipped': skipped})
    return out, {'applied': any(p['changes'] for p in report), 'version': VERSION, 'phrases': report,
                 'skipped': sum(bool(p['skipped']) for p in report)}


SKIPPED = ('inconsistent_word_times', 'skipped_overlap')


def summary(phrases, vocal, duration_ms):
    """Plain numbers for audits: intro, breaks, pauses, singing without text."""
    rows = _intervals(vocal)
    if rows is None:
        return {'reliable': False}
    lyric = sorted([p for p in phrases or [] if str(p.get('text') or '').strip()
                    and p.get('vocal_evidence') != 'none'], key=lambda p: int(p.get('start_ms') or 0))
    parts, cursor = [], 0
    for p in lyric + [{'start_ms': duration_ms or cursor, 'end_ms': duration_ms or cursor}]:
        a = int(p.get('start_ms') or 0)
        if a > cursor:
            parts.extend(split_gap(vocal, cursor, a, duration_ms))
        cursor = max(cursor, int(p.get('end_ms') or 0))
    return {'reliable': True, 'first_voice_ms': rows[0][0] if rows else None,
            'first_lyric_ms': int(lyric[0]['start_ms']) if lyric else None,
            'breaks': [[a, b] for a, b, k in parts if k == 'break'],
            'pauses': sum(k == 'pause' for _, _, k in parts),
            'voiced_gaps': [[a, b] for a, b, k in parts if k == 'vocal'],
            'unvoiced_lyrics': [{'phrase_id': p.get('phrase_id'), 'start_ms': p.get('start_ms'),
                                 'end_ms': p.get('end_ms'), 'text': str(p.get('text'))[:60]}
                                for p in phrases or [] if p.get('vocal_evidence') == 'none'],
            'duration_ms': duration_ms}


def sung_gaps(phrases, vocal, duration_ms=None):
    """``(start, end)`` of singing without recognized text (see ``split_gap``)."""
    if _intervals(vocal) is None:
        return []
    lyric = sorted([p for p in phrases or [] if str(p.get('text') or '').strip()
                    and p.get('vocal_evidence') != 'none'], key=lambda p: int(p.get('start_ms') or 0))
    out, cursor = [], 0
    end = duration_ms or max([int(p.get('end_ms') or 0) for p in lyric] + [0])
    for p in lyric + [{'start_ms': end, 'end_ms': end}]:
        a = int(p.get('start_ms') or 0)
        if a > cursor:
            out.extend((x, y) for x, y, kind in split_gap(vocal, cursor, a, duration_ms) if kind == 'vocal')
        cursor = max(cursor, int(p.get('end_ms') or 0))
    return out


PLACEHOLDER = '???'


def placeholder_rows(phrases, vocal, duration_ms=None):
    """Editable lines «???» where somebody sings and no text was recognized.

    Same passages as «Пение — текст не распознан» (voice in the stem and words
    an independent recognizer heard). A line, not a label: the user opens it in
    the phrase editor and types the words over the question marks.
    """
    return [{'phrase_id': f'sung-placeholder-{a}', 'text': PLACEHOLDER, 'start_ms': int(a), 'end_ms': int(b),
             'words': [], 'timing_quality': 'phrase', 'alignment_stale': True, 'kind': 'unknown', 'note': '',
             'review_state': 'needs_review', 'placeholder': True,
             'alignment_warning': 'Здесь поют, но слова не распознаны — впишите текст вместо «???»'}
            for a, b in sung_gaps(phrases, vocal, duration_ms)]


def insert_placeholders(phrases, vocal, duration_ms=None):
    """``(phrases, count)`` with a «???» line in every unrecognized sung passage."""
    out = copy.deepcopy(phrases or [])
    rows = placeholder_rows(out, vocal, duration_ms)
    for row in rows:
        index = next((i for i, p in enumerate(out) if int(p.get('start_ms') or 0) > row['start_ms']), len(out))
        out.insert(index, row)
    return out, len(rows)


def _words(text):
    return [(m.group(), re.sub(r'[^\w]+', '', m.group().casefold().replace('ё', 'е')))
            for m in re.finditer(r'\S+', str(text or ''))]


def agreed_text(first, second, min_words=3):
    """Words two independent recognizers agree on, in the first one's spelling.

    Both readings must cover the same passage (≥ 70 % of the shorter one
    matched) and may differ by at most one word between agreed runs; the
    result never contains a word only one model heard at its edges.
    """
    from difflib import SequenceMatcher
    a = [w for w in _words(first) if w[1]]
    b = [w for w in _words(second) if w[1]]
    if len(a) < min_words or len(b) < min_words:
        return ''
    blocks = [x for x in SequenceMatcher(None, [w[1] for w in a], [w[1] for w in b],
                                         autojunk=False).get_matching_blocks() if x.size]
    matched = sum(x.size for x in blocks)
    if matched < min_words or matched < .7 * min(len(a), len(b)):
        return ''
    for left, right in zip(blocks, blocks[1:]):
        if right.a - (left.a + left.size) > 1 or right.b - (left.b + left.size) > 1:
            return ''
    return ' '.join(w[0] for w in a[blocks[0].a:blocks[-1].a + blocks[-1].size])


def repeated_line(first, second, phrases, start_ms, end_ms, min_core=4):
    """Settle a two-model disagreement with the same words sung elsewhere.

    Songs repeat: a chorus line both recognizers half-heard at the opening
    is often recognized cleanly where it returns. Take the passage of the
    song's own text that both fresh readings support (≥ 60 % each, a shared
    run of ≥ 4 words) from lines at least 10 s away — a neighbour line would
    be the same occurrence with shifted times, not a repeat.
    """
    from difflib import SequenceMatcher
    a = [w[1] for w in _words(first) if w[1]]
    b = [w[1] for w in _words(second) if w[1]]
    if len(a) < 3 or len(b) < 3:
        return ''
    core = max((x.size for x in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()), default=0)
    if core < min_core:
        return ''
    song = [w for p in phrases or [] if str(p.get('text') or '').strip() and p.get('vocal_evidence') != 'none'
            and (int(p.get('start_ms') or 0) > end_ms + 10000 or int(p.get('end_ms') or 0) < start_ms - 10000)
            for w in _words(p.get('text')) if w[1]]
    norms = [w[1] for w in song]

    def matched(span, reading):
        return [x for x in SequenceMatcher(None, span, reading, autojunk=False).get_matching_blocks() if x.size]
    best = None
    for size in range(max(3, len(a) - 3), len(a) + 4):
        for i in range(0, len(norms) - size + 1):
            span = norms[i:i + size]
            ma, mb = matched(span, a), matched(span, b)
            sa, sb = sum(x.size for x in ma), sum(x.size for x in mb)
            if sa < .6 * size or sb < .6 * size or max(x.size for x in ma) < min_core:
                continue
            if best is None or sa + sb > best[0]:
                covered = {x.a + k for blocks in (ma, mb) for x in blocks for k in range(x.size)}
                best = (sa + sb, i, size, covered)
    if best is None:
        return ''
    _, i, size, covered = best
    first_kept, last_kept = min(covered), max(covered)
    return ' '.join(w[0] for w in song[i + first_kept:i + last_kept + 1])


def trim_neighbors(text, previous='', following=''):
    """Drop words of the neighbouring lines that a wider crop also heard."""
    words = _words(text)
    before = [w[1] for w in _words(previous)][-4:]
    after = [w[1] for w in _words(following)][:4]
    start = 0
    for size in range(min(len(before), len(words)), 0, -1):
        if [w[1] for w in words[:size]] == before[-size:]:
            start = size
            break
    end = len(words)
    for size in range(min(len(after), len(words) - start), 0, -1):
        if [w[1] for w in words[len(words) - size:]] == after[:size]:
            end = len(words) - size
            break
    return ' '.join(w[0] for w in words[start:end])


def insert_recovered(phrases, text, words, window, models, phrase_id, basis='two_models', gap=None):
    """New line for recovered singing; existing lines, ids and edits untouched.

    With some words unmeasured the line is ``word_partial`` (the sheet
    estimates those words between their measured neighbours) and its edges
    fall back to the voiced gap ``gap`` = (start, end).
    """
    complete = len(words) == len(text.split())
    start, end = int(words[0]['start_ms']), int(words[-1]['end_ms'])
    if gap and words[0].get('char_start') != 0:
        start = min(start, int(gap[0]))
    if gap and words[-1].get('char_end') != len(text):
        end = max(end, int(gap[1]))
    row = {'phrase_id': phrase_id, 'text': text, 'start_ms': start,
           'end_ms': end, 'words': copy.deepcopy(words),
           'timing_quality': 'word_estimated' if complete else 'word_partial', 'alignment_stale': False,
           'kind': 'unknown', 'note': '', 'review_state': 'needs_review',
           'recovered': {'method': 'two_model_sung_gap', 'basis': basis, 'window_ms': list(window),
                         'models': list(models)}}
    if not complete:
        row['alignment_warning'] = 'Часть слов имеет измеренные времена; остальные подсвечиваются приблизительно'
    out = copy.deepcopy(phrases or [])
    index = next((i for i, p in enumerate(out) if int(p.get('start_ms') or 0) > row['start_ms']), len(out))
    out.insert(index, row)
    return out


# ---------------------------------------------------------------- grid runs
#
# Whisper sometimes loses its timing inside a sung passage and writes
# contiguous segments on a whole-second grid: 30–32, 32–34, 34–36 … s.
# A measured timestamp has 20 ms resolution, so three contiguous
# whole-second segments are a grid, not a measurement. Each line was then
# aligned only inside its own two-second crop and could not move to where
# it is sung («Прямо сейчас» at 33 s instead of 36.5 s, the whole verse one
# line early). Such lines are aligned again as one passage.

GRID_SHORT_MS = 3000
GRID_SEGMENT_MS = 4000
GRID_MIN_SHORT = 3
GRID_LEAD_MS = 4000
GRID_TAIL_MS = 8000
GRID_MIN_MEASURED = 0.6
GRID_MIN_VOICED = 0.8


def _whole_second(value):
    return finite(value) and int(value) == value and int(value) % 1000 == 0


def _grid_rows(segments):
    """Contiguous whole-second segments of one recognizer, as lists."""
    rows = sorted((p for p in segments or [] if isinstance(p, dict) and p.get('timing_quality') != 'chunk'
                   and _whole_second(p.get('start_ms')) and _whole_second(p.get('end_ms'))
                   and p['end_ms'] > p['start_ms']), key=lambda p: (p['start_ms'], p['end_ms']))
    runs, current = [], []
    for row in rows:
        if current and row['start_ms'] == current[-1]['end_ms']:
            current.append(row)
        else:
            if current:
                runs.append(current)
            current = [row]
    if current:
        runs.append(current)
    return [r for r in runs if sum(p['end_ms'] - p['start_ms'] <= GRID_SHORT_MS for p in r) >= GRID_MIN_SHORT]


def grid_runs(segments):
    """``[(start, end)]`` of contiguous whole-second segments of one recognizer."""
    return [(int(r[0]['start_ms']), int(r[-1]['end_ms'])) for r in _grid_rows(segments)]


def grid_spans(hypotheses):
    """Grid runs of all recognizers, overlapping spans merged."""
    spans = sorted(r for h in hypotheses or [] if isinstance(h, dict) for r in grid_runs(h.get('phrases')))
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def grid_segments(hypotheses):
    """Short segments of every grid run: the crops lines were confined to."""
    return [dict(start_ms=int(p['start_ms']), end_ms=int(p['end_ms']), text=str(p.get('text') or ''))
            for h in hypotheses or [] if isinstance(h, dict) for run in _grid_rows(h.get('phrases'))
            for p in run if p['end_ms'] - p['start_ms'] <= GRID_SEGMENT_MS]


def _coverage(text, other):
    from difflib import SequenceMatcher
    a = [w for _, w in _words(text) if w]
    b = [w for _, w in _words(other) if w]
    if not a or not b:
        return 0.0
    return sum(x.size for x in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / len(a)


def _grid_protected(phrase):
    return bool(phrase.get('timing_quality') == 'manual_phrase' or phrase.get('timing_locked')
                or phrase.get('recovered') or phrase.get('manual_edited')
                or any(w.get('method') == 'manual' or w.get('manual') for w in phrase.get('words') or []))


def grid_groups(phrases, segments, slack=500):
    """``[(indexes, span)]``: consecutive lines each confined to a grid segment.

    A line belongs when it lies inside one grid segment (± ``slack``) whose
    words it shares — i.e. it was aligned in that synthetic crop. A line the
    user timed (or a recovered line) splits the passage; it is never moved.
    """
    out, current, span = [], [], None

    def close():
        if len(current) >= 2:
            out.append((list(current), tuple(span)))

    for i, phrase in enumerate(phrases or []):
        if not str(phrase.get('text') or '').strip():
            continue
        a, b = int(phrase.get('start_ms') or 0), int(phrase.get('end_ms') or 0)
        seg = None if _grid_protected(phrase) else next(
            (g for g in segments or [] if g['start_ms'] - slack <= a and b <= g['end_ms'] + slack
             and _coverage(phrase.get('text'), g['text']) >= .5), None)
        if seg is not None and current and current[-1] == i - 1:
            current.append(i)
            span = [min(span[0], seg['start_ms']), max(span[1], seg['end_ms'])]
            continue
        close()
        current, span = ([i], [seg['start_ms'], seg['end_ms']]) if seg is not None else ([], None)
    close()
    return out


def grid_window(phrases, indexes, span, duration_ms=None, vocal=None):
    """Crop for one joint alignment: from the previous sung line to the next.

    It always covers the lines' current extent. The voiced tail of the
    previous line (a held last word) is left out of the crop.
    """
    a = min(span[0], int(phrases[indexes[0]].get('start_ms') or 0))
    b = max(span[1], int(phrases[indexes[-1]].get('end_ms') or 0))
    before = [p for p in phrases[:indexes[0]] if str(p.get('text') or '').strip() and p.get('vocal_evidence') != 'none']
    after = [p for p in phrases[indexes[-1] + 1:] if str(p.get('text') or '').strip() and p.get('vocal_evidence') != 'none']
    previous_end = int(before[-1]['end_ms']) if before else None
    start = max(previous_end or 0, a - GRID_LEAD_MS, 0)
    if previous_end is not None:
        held = next(((x, y) for x, y in _intervals(vocal) or [] if x <= previous_end < y and x <= start < y), None)
        if held and held[1] < a:
            start = held[1]
    end = b + GRID_TAIL_MS
    if after:
        end = min(end, int(after[0]['start_ms']))
    if duration_ms:
        end = min(end, int(duration_ms))
    return start, max(start + 1, end)


def grid_alignment_quality(words, count, vocal):
    """``(ok, reason, voiced_share)`` for one joint alignment of a passage."""
    if not words or len(words) < max(3, math.ceil(GRID_MIN_MEASURED * max(1, count))):
        return False, 'alignment_incomplete', 0.0
    if any(b['start_ms'] < a['end_ms'] for a, b in zip(words, words[1:])):
        return False, 'alignment_unordered', 0.0
    total = sum(int(w['end_ms']) - int(w['start_ms']) for w in words)
    weak = vocal.get('weak') if isinstance(vocal, dict) and vocal.get('weak') is not None else vocal
    share = sum(voiced_ms(weak, int(w['start_ms']), int(w['end_ms'])) for w in words) / max(1, total)
    if share < GRID_MIN_VOICED:
        return False, 'alignment_outside_voice', round(share, 3)
    return True, None, round(share, 3)


def _voiced_around(vocal, at, lower, upper, side):
    """Edge of the voiced stretch that holds ``at`` (or the next one toward it)."""
    rows = _intervals(vocal) or []
    if side == 'start':
        hits = [x for x, y in rows if x <= at and y > lower]
        return max(lower, hits[-1]) if hits else lower
    hits = [y for x, y in rows if y >= at and x < upper]
    return min(upper, hits[0]) if hits else upper


def grid_edge_words(words, window, vocal):
    """Drop words pinned to a crop edge that lies inside singing.

    Words the aligner cannot place end up glued to the start (or end) of the
    crop; where the crop edge cuts through a voiced stretch that is not a
    measurement but a leftover.
    """
    a, b = window
    rows = _intervals(vocal) or []
    voiced_start = any(x < a + 100 and y > a for x, y in rows)
    voiced_end = any(x < b and y > b - 100 for x, y in rows)
    return [w for w in words if not (voiced_start and w['start_ms'] <= a + 80)
            and not (voiced_end and w['end_ms'] >= b - 80)]


def apply_grid_words(phrases, indexes, words, window, vocal=None, info=None):
    """Spread one joint alignment back over its lines; text and order untouched.

    Measured words keep their times. A line whose first (last) word was not
    measured begins (ends) with the voiced stretch around its first (last)
    measured word; a line without any measured word lies between its
    neighbours and is marked for review.
    """
    out = copy.deepcopy(phrases)
    own, offset = {}, 0
    for i in indexes:
        text = str(out[i].get('text') or '')
        own[i] = [dict(w, char_start=w['char_start'] - offset, char_end=w['char_end'] - offset)
                  for w in words if offset <= w['char_start'] and w['char_end'] <= offset + len(text)]
        offset += len(text) + 1
    a, b = window
    bounds = {}
    for k, i in enumerate(indexes):
        mine = own[i]
        if not mine:
            continue
        tokens = list(re.finditer(r'\S+', str(out[i].get('text') or '')))
        lower = next((own[j][-1]['end_ms'] for j in reversed(indexes[:k]) if own[j]), a)
        upper = next((own[j][0]['start_ms'] for j in indexes[k + 1:] if own[j]), b)
        start, end = int(mine[0]['start_ms']), int(mine[-1]['end_ms'])
        if tokens and mine[0]['char_start'] != tokens[0].start():
            start = _voiced_around(vocal, start, int(lower), start, 'start')
        if tokens and mine[-1]['char_end'] != tokens[-1].end():
            end = _voiced_around(vocal, end, end, max(end, int(upper)), 'end')
        bounds[i] = (start, end)
    for k, i in enumerate(indexes):
        phrase = out[i]
        tokens = list(re.finditer(r'\S+', str(phrase.get('text') or '')))
        mine = own[i]
        phrase.setdefault('grid_source_ms', [int(phrase.get('start_ms') or 0), int(phrase.get('end_ms') or 0)])
        for name in ('timing_fit', 'measured_start_ms', 'measured_end_ms', 'vocal_evidence', 'onset_check',
                     'vocal_alignment_check', 'intro_alignment_check'):
            phrase.pop(name, None)
        phrase['grid_realigned'] = dict(info or {}, window_ms=[int(a), int(b)])
        previous_end = int(out[indexes[k - 1]]['end_ms']) if k else a
        if mine:
            start, end = bounds[i]
            complete = len(mine) == len(tokens)
            phrase.update(words=mine, alignment_stale=False, start_ms=max(previous_end, start), end_ms=end,
                          timing_quality='word_estimated' if complete else 'word_partial')
            if complete:
                phrase.pop('alignment_warning', None)
            else:
                phrase['alignment_warning'] = 'Часть слов имеет измеренные времена; остальные подсвечиваются приблизительно'
        else:
            following = next((bounds[j][0] for j in indexes[k + 1:] if j in bounds), b)
            start = previous_end
            phrase.update(words=[], alignment_stale=True, start_ms=start, end_ms=max(start + 1, int(following)),
                          timing_quality='phrase',
                          alignment_warning='Строка не найдена при повторном выравнивании; время приблизительное')
    return out


def remap_sections(sections, old_phrases, new_phrases):
    """Keep section membership by phrase id after lines were inserted."""
    old_ids = [p.get('phrase_id') for p in old_phrases or []]
    index = {p.get('phrase_id'): i for i, p in enumerate(new_phrases or [])}
    out = copy.deepcopy(sections or [])
    for section in out:
        section['phrase_indexes'] = sorted(index[old_ids[i]] for i in section.get('phrase_indexes', [])
                                           if 0 <= i < len(old_ids) and old_ids[i] in index)
    return out


def merge_timing(current, fresh):
    """Carry new timings onto the current phrases without touching their text.

    Phrases are matched by id, then by identical normalized text near the same
    time (unique match only). The phrase position in the list is never used:
    a rerun may split, merge or drop a phrase, and an index match would move a
    line's time onto its neighbour. A same-id phrase whose text the user edited
    receives only its edges; word times stay with identical text only.
    """
    def key(phrase):
        return re.sub(r'[^\w]+', '', str(phrase.get('text') or '').casefold().replace('ё', 'е'))
    fresh = [p for p in fresh or [] if isinstance(p, dict)]
    by_id = {p.get('phrase_id'): p for p in fresh if p.get('phrase_id')}
    used, out, matched = set(), [], 0
    fields = ('start_ms', 'end_ms', 'timing_quality', 'alignment_warning', 'alignment_stale',
              'measured_start_ms', 'measured_end_ms', 'timing_fit', 'vocal_evidence', 'grid_realigned',
              'grid_source_ms')
    for phrase in current or []:
        updated = copy.deepcopy(phrase)
        out.append(updated)
        if phrase.get('timing_quality') == 'manual_phrase' or phrase.get('timing_locked'):
            continue
        match = by_id.get(phrase.get('phrase_id'))
        if match is not None and id(match) in used:
            match = None
        if match is None or key(match) != key(phrase):
            near = [p for p in fresh if id(p) not in used and key(p) == key(phrase) and key(p)
                    and abs(int(p.get('start_ms') or 0) - int(phrase.get('start_ms') or 0)) <= 15000]
            if len(near) == 1:
                match = near[0]
        if match is None:
            continue
        used.add(id(match))
        matched += 1
        if key(match) != key(phrase):
            updated['start_ms'], updated['end_ms'] = int(match['start_ms']), int(match['end_ms'])
            continue
        for name in fields:
            if name in match:
                updated[name] = copy.deepcopy(match[name])
            else:
                updated.pop(name, None)
        if match.get('text') == phrase.get('text'):
            updated['words'] = copy.deepcopy(match.get('words') or [])
    return out, matched


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
