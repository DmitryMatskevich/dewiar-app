"""Chord positions: manual/measured anchors first, labelled estimates otherwise."""
from __future__ import annotations
import re


def clean_chord(value):
    value = re.sub(r'\s+', '', str(value or '').strip())
    if not value or len(value) > 24 or not re.fullmatch(r'[A-Ga-g](?:#|b)?(?:m7b5|(?:m|maj|min|dim|aug|sus|add)?\d*)(?:/[A-Ga-g](?:#|b)?)?', value):
        return ''
    return value[0].upper() + value[1:]


def _voicing_fields(event):
    """Keep an exact guitar position only when an upstream source supplied it.

    Chordino/CQT names do not contain string/fret information, so this data
    must never be manufactured from the chord name.  A source-aware voicing
    may use ``0`` for an open string, ``x`` for an unplayed string and a fret
    number for strings 1→6.
    """
    value=event.get('voicing_fingering', event.get('fingering'))
    if isinstance(value, (list, tuple)):
        value='-'.join(str(item).strip() for item in value)
    value=str(value or '').strip()
    if not re.fullmatch(r'(?:x|[0-9]|1[0-9]|2[0-4])(?:-(?:x|[0-9]|1[0-9]|2[0-4])){5}', value):
        return {}
    probability=event.get('voicing_probability', event.get('fingering_probability'))
    try:
        probability=float(probability)
    except (TypeError, ValueError):
        probability=None
    fields={'voicing_fingering':value, 'voicing_source':str(event.get('voicing_source') or 'source').strip()[:80]}
    if probability is not None and 0 <= probability <= 1:
        fields['voicing_probability']=round(probability, 4)
    return fields


def _vocal_bounds(phrase):
    """Use timed words to exclude instrumental lead-ins and tails."""
    start = int(phrase.get('start_ms') or 0)
    end = int(phrase.get('end_ms') or start)
    words = [word for word in phrase.get('words') or []
             if isinstance(word.get('start_ms'), (int, float))
             and isinstance(word.get('end_ms'), (int, float))
             and word.get('end_ms') > word.get('start_ms')]
    if words and not phrase.get('alignment_stale'):
        if phrase.get('timing_quality') == 'word_partial':
            from .intro_alignment import bounds_with_words
            return bounds_with_words(phrase, sorted(words,key=lambda w:w.get('char_start',0)))
        start = min(int(word['start_ms']) for word in words)
        end = max(int(word['end_ms']) for word in words)
    return start, max(start, end)


def _anchor(event, phrase):
    anchor = event.get('anchor') or {}
    if anchor.get('phrase_id') == phrase.get('phrase_id') and anchor.get('kind') in {'manual', 'char_ctc'}:
        index = anchor.get('grapheme_index')
        if isinstance(index,int):
            index -= phrase.get('source_char_offset',0)
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(phrase['text']):
            return index, anchor['kind']
    if not phrase.get('alignment_stale'):
        for word in phrase.get('words') or []:
            if (word.get('start_ms') is not None and word.get('end_ms') is not None
                    and word['start_ms'] <= event['start_ms'] < word['end_ms']
                    and isinstance(word.get('char_start'), int)
                    and 0 <= word['char_start'] < len(phrase['text'])):
                return word['char_start'], 'word'
    # Display estimate only: never write guessed word timestamps into ASR data.
    words = list(re.finditer(r'\S+', phrase['text']))
    duration = phrase.get('end_ms',0)-phrase.get('start_ms',0)
    if words and duration > 0:
        reliable = [] if phrase.get('alignment_stale') else [w for w in phrase.get('words',[])
            if isinstance(w.get('char_start'),int) and 0 <= w['char_start'] < len(phrase['text'])
            and w.get('start_ms') is not None and w.get('end_ms',0)>w['start_ms']]
        if reliable:
            nearest = min(reliable,key=lambda w:abs(w['start_ms']-event['start_ms']))
            if abs(nearest['start_ms']-event['start_ms']) <= 1200:
                return nearest['char_start'], 'estimated_nearest_word'
        ratio = max(0,min(1,(event['start_ms']-phrase['start_ms'])/duration))
        # Syllable weights give long sung words more room than articles/prepositions.
        weights = [max(1,len(re.findall(r'[аеёиоуыэюяaeiouy]',w.group(),re.I))) for w in words]
        target, accumulated = ratio*sum(weights),0
        for word,weight in zip(words,weights):
            accumulated += weight
            if target < accumulated:
                return word.start(), 'estimated_phrase'
        return words[-1].start(), 'estimated_phrase'
    return None, 'line'


def _vocal_slices(phrase):
    """Split only measured pauses between adjacent text tokens, preserving edits."""
    text = str(phrase.get('text') or '')
    if phrase.get('alignment_stale'):
        return [phrase]
    words = sorted([w for w in phrase.get('words',[]) if
                    isinstance(w.get('char_start'),int) and isinstance(w.get('char_end'),int) and
                    isinstance(w.get('start_ms'),(int,float)) and isinstance(w.get('end_ms'),(int,float)) and
                    w['end_ms']>w['start_ms']],key=lambda w:w['char_start'])
    cuts = [0]
    for a,b in zip(words,words[1:]):
        gap = b['start_ms'] - a['end_ms']
        between = text[a['char_end']:b['char_start']]
        boundary = ('\n' in between or text[b['char_start']:b['char_start']+1].isupper()
                    or bool(re.search(r'[.!?;][»”\"\)]?$', text[:a['char_end']])))
        if (gap > 0 and (boundary or gap >= 1500) and not between.strip()):
            cuts.append(b['char_start'])
    if len(cuts)==1:
        return [phrase]
    slices=[]
    for a,b in zip(cuts,cuts[1:]+[len(text)]):
        value=text[a:b].rstrip()
        selected=[dict(w,char_start=w['char_start']-a,char_end=w['char_end']-a)
                  for w in words if a<=w['char_start'] and w['char_end']<=a+len(value)]
        slices.append({**phrase,'text':value,'words':selected,'source_char_offset':a,
                       'start_ms':phrase['start_ms'] if a==0 else selected[0]['start_ms'],
                       'end_ms':phrase['end_ms'] if b==len(text) else selected[-1]['end_ms']})
    return slices


def held_spans(spans, vocal, duration_ms=None):
    """Line spans that end with the voice they end in.

    The aligner ends the last word at its consonants; the held vowel goes on
    in the stem («Ты так хороша-а»). A chord changed under it fell into the
    pause after the line and showed on a line of its own, without text, while
    the line above was still sung. The line keeps that voiced stretch, up to
    the next line and at most ``HELD_MS`` (the same rule as an unmeasured
    last word in ``vocal_timing``).
    """
    from .vocal_timing import _held_end
    starts = sorted(a for a, _ in spans)
    out = []
    for a, b in spans:
        nxt = next((s for s in starts if s >= b), None)
        if duration_ms:
            nxt = min(nxt, int(duration_ms)) if nxt is not None else int(duration_ms)
        out.append((a, max(b, _held_end(vocal, b, nxt))))
    return out


def layout_chords(phrases, events, duration_ms=None, vocal=None):
    """Lyric rows with chords plus the gaps between them.

    ``vocal`` is the vocal activity of the recording (``vocal_timing``). With
    it a gap is a break only where nobody sings; a voiced stretch without
    recognized words becomes its own row marked ``vocal`` instead of being
    called an instrumental break.
    """
    from .vocal_timing import split_gap
    valid = []
    for event in events or []:
        if not isinstance(event, dict):
            raise ValueError('Аккорд должен быть объектом')
        name = clean_chord(event.get('name'))
        if not name:
            continue
        start = int(event.get('start_ms', 0))
        if start < 0:
            raise ValueError('Время аккорда не может быть отрицательным')
        voicing=_voicing_fields(event)
        base={key:value for key,value in event.items()
              if key not in {'voicing_fingering','fingering','voicing_probability','fingering_probability','voicing_source'}}
        valid.append({**base, **voicing, 'name': name, 'start_ms': start})
    valid.sort(key=lambda e: e['start_ms'])
    out = []
    # Text placed where the vocal stem is silent is not a sung line: it is
    # listed with the break it falls into, for review, and does not cut the
    # intro or a break in two.
    unsung = [p for p in phrases or [] if vocal and p.get('vocal_evidence') == 'none'
              and str(p.get('text') or '').strip()]
    sung = [p for p in phrases or [] if not any(p is q for q in unsung)]
    slices = [part for p in sung for part in _vocal_slices(p) if str(part.get('text') or '')]
    spans = [_vocal_bounds(part) for part in slices]
    if vocal:
        spans = held_spans(spans, vocal, duration_ms)
    # Each chord change belongs to ONE line: the narrowest line sounding at
    # that moment. Overlapping recognitions (one line stretched over a minute)
    # must not swallow the changes of the lines inside it, leaving those
    # lines without chords.
    owner = {}
    for k, event in enumerate(valid):
        best = None
        for j, (a, b) in enumerate(spans):
            if a <= event['start_ms'] < b and (best is None or b - a < spans[best][1] - spans[best][0]):
                best = j
        owner[k] = best
    for index, phrase in enumerate(slices):
        text = str(phrase.get('text') or '')
        ps, pe = int(phrase.get('start_ms') or 0), int(phrase.get('end_ms') or 0)
        vocal_ps, vocal_pe = spans[index]
        sung_ps, sung_pe = _vocal_bounds(phrase)
        tokens = list(re.finditer(r'\S+', text))
        marks, unplaced = [], []
        # A lyric line also needs the chord already sounding at its start.
        # This is a continuation, not an invented new change. N stops carry-in.
        previous = [e for e in events or []
                    if int(e.get('start_ms', 0)) < vocal_ps
                    and (e.get('end_ms') is None or int(e.get('end_ms') or 0) > vocal_ps)]
        active = max(previous, key=lambda e: int(e.get('start_ms',0)), default=None)
        if active and clean_chord(active.get('name')) and (active.get('end_ms') is None or active['end_ms'] > vocal_ps):
            marks.append({**active, 'char_index':0, 'anchor_quality':'continuation', 'continued':True})
        for k, event in enumerate(valid):
            if owner.get(k) != index:
                continue
            at, quality = _anchor(event, {**phrase, 'start_ms': sung_ps,
                                          'end_ms': sung_pe, 'text': text})
            if event['start_ms'] >= sung_pe and tokens and quality not in ('manual', 'char_ctc'):
                # Changed under the held last vowel: over the last word.
                at, quality = tokens[-1].start(), 'held_word'
            mark = {**event, 'char_index': at, 'anchor_quality': quality}
            (marks if at is not None else unplaced).append(mark)
        grouped = {}
        for mark in marks:
            grouped.setdefault(mark['char_index'], []).append(mark['name'])
        positions = sorted(set([0, *grouped]))
        cells = [{'text': text[pos:(positions[j+1] if j+1 < len(positions) else len(text))],
                  'chords': grouped.get(pos, []), 'char_index': pos} for j,pos in enumerate(positions)]
        out.append({**phrase, 'cells': cells, 'chords': marks, 'unplaced_chords': unplaced,
                    'start_ms':vocal_ps,'end_ms':vocal_pe,
                    'timing_quality': 'estimated' if any(m.get('anchor_quality','').startswith('estimated') for m in marks) else 'anchored' if marks and not unplaced else 'line'})
    # Instrumental intervals are the complement of vocal intervals, not a side
    # effect of a chord changing. Include sub-second intervals and held chords.
    vocal_rows = sorted(out, key=lambda r: r['start_ms'])
    duration = max([duration_ms or 0, *[r['end_ms'] for r in vocal_rows],
                    *[int(e.get('end_ms') or e['start_ms']) for e in valid]])
    cursor = 0
    for row in [*vocal_rows, {'start_ms': duration, 'end_ms': duration}]:
        finish = row['start_ms']
        if finish > cursor:
            changes = []
            for i, event in enumerate(valid):
                end = event.get('end_ms')
                if end is None:
                    end = valid[i+1]['start_ms'] if i+1 < len(valid) else duration
                if event['start_ms'] < finish and end > cursor:
                    changes.append((dict(event), int(end)))
            for part_start, part_end, kind in split_gap(vocal, cursor, finish, duration):
                # Unknown harmony must not erase an otherwise timed vocal break.
                held = [dict(event) for event, end in changes
                        if event['start_ms'] < part_end and end > part_start]
                if kind == 'pause':
                    # Between two lines only a passing chord needs its own
                    # place; a chord still sounding at the next line is shown
                    # over its first word, and the previous one is already up.
                    held = [dict(event) for event, end in changes
                            if part_start <= event['start_ms'] < part_end and end <= finish]
                gap = {'text':'', 'start_ms':part_start, 'end_ms':part_end, 'cells':[], 'chords':[],
                       'unplaced_chords':held, 'instrumental':True, 'timing_quality':'gap'}
                if kind != 'break':
                    gap[kind] = True
                notes = [{'phrase_id': p.get('phrase_id'), 'text': p.get('text')} for p in unsung
                         if part_start <= int(p.get('start_ms') or 0) < part_end]
                if notes:
                    gap['unsung'] = notes
                out.append(gap)
        cursor = max(cursor, row['end_ms'])
    return sorted(out, key=lambda row: row['start_ms'])


def build_guitar_sheet(phrases, events, duration_ms=None, vocal=None):
    return layout_chords(phrases, events, duration_ms, vocal)
