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
            first=next((w for w in words if w.get('char_start')==0),None)
            last=next((w for w in words if w.get('char_end')==len(phrase.get('text',''))),None)
            return (int(first['start_ms']) if first else start,
                    int(last['end_ms']) if last else end)
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
        if (b['start_ms']-a['end_ms']>=2500 and
                not text[a['char_end']:b['char_start']].strip()):
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


def layout_chords(phrases, events):
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
    out, used = [], set()
    for phrase in [part for p in phrases or [] for part in _vocal_slices(p)]:
        text = str(phrase.get('text') or '')
        if not text:
            continue
        ps, pe = int(phrase.get('start_ms') or 0), int(phrase.get('end_ms') or 0)
        vocal_ps, vocal_pe = _vocal_bounds(phrase)
        marks, unplaced = [], []
        # A lyric line also needs the chord already sounding at its start.
        # This is a continuation, not an invented new change. N stops carry-in.
        previous = [e for e in events or []
                    if int(e.get('start_ms', 0)) < vocal_ps
                    and (e.get('end_ms') is None or int(e.get('end_ms') or 0) > vocal_ps)]
        active = max(previous, key=lambda e: int(e.get('start_ms',0)), default=None)
        if active and clean_chord(active.get('name')) and (active.get('end_ms') is None or active['end_ms'] > vocal_ps):
            marks.append({**active, 'char_index':0, 'anchor_quality':'continuation', 'continued':True})
        for i, event in enumerate(valid):
            event_end = int(event.get('end_ms') or event['start_ms'])
            if vocal_ps <= event['start_ms'] < vocal_pe:
                used.add(i)
            elif event['start_ms'] < vocal_ps and event_end > vocal_ps:
                # A chord crossing the first word is already shown as a
                # carry-in chord; do not duplicate it in the instrumental row.
                used.add(i)
                continue
            else:
                continue
            at, quality = _anchor(event, {**phrase, 'start_ms': vocal_ps,
                                          'end_ms': vocal_pe, 'text': text})
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
    # Смены в проигрыше/паузе тоже нужны: не теряем их из-за отсутствия текста.
    for i,event in enumerate(valid):
        if i not in used:
            out.append({'text': '', 'start_ms': event['start_ms'], 'end_ms': event['start_ms'],
                        'cells': [], 'chords': [], 'unplaced_chords': [event], 'timing_quality': 'gap'})
    # A sustained chord can cover a real break without a new change inside it.
    # Keep that instrumental cue too; no chord is invented for unvoiced silence.
    vocal_rows=sorted((r for r in out if r.get('text')),key=lambda r:r['start_ms'])
    cursor=0
    for row in vocal_rows:
        finish=row['start_ms']
        if finish-cursor>=1500 and not any(not r.get('text') and cursor<=r['start_ms']<finish for r in out):
            active=max((e for e in events or [] if e.get('start_ms',0)<=cursor),
                       key=lambda e:e.get('start_ms',0),default=None)
            changes=[e for e in valid if cursor<=e['start_ms']<finish]
            if active and clean_chord(active.get('name')) and (active.get('end_ms') is None or active['end_ms']>cursor):
                changes.insert(0,active)
            if changes:
                out.append({'text':'','start_ms':cursor,'end_ms':finish,'cells':[],'chords':[],
                            'unplaced_chords':[dict(e) for e in changes],'timing_quality':'gap'})
        cursor=max(cursor,row['end_ms'])
    ordered = sorted(out, key=lambda row: row.get('start_ms', 0))
    grouped_rows = []
    for row in ordered:
        if (row.get('timing_quality') == 'gap' and grouped_rows and
                grouped_rows[-1].get('timing_quality') == 'gap' and
                row['start_ms']-grouped_rows[-1].get('end_ms', grouped_rows[-1]['start_ms']) <= 2_500):
            grouped_rows[-1]['unplaced_chords'].extend(row['unplaced_chords'])
            grouped_rows[-1]['end_ms'] = row['end_ms']
        else:
            grouped_rows.append(row)
    return grouped_rows


def build_guitar_sheet(phrases, events):
    return layout_chords(phrases, events)
