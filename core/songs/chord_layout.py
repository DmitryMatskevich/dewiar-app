"""Chord positions: manual/measured anchors first, labelled estimates otherwise."""
from __future__ import annotations
import re


def clean_chord(value):
    value = re.sub(r'\s+', '', str(value or '').strip())
    if not value or len(value) > 24 or not re.fullmatch(r'[A-Ga-g](?:#|b)?(?:m7b5|(?:m|maj|min|dim|aug|sus|add)?\d*)(?:/[A-Ga-g](?:#|b)?)?', value):
        return ''
    return value[0].upper() + value[1:]


def _anchor(event, phrase):
    anchor = event.get('anchor') or {}
    if anchor.get('phrase_id') == phrase.get('phrase_id') and anchor.get('kind') in {'manual', 'char_ctc'}:
        index = anchor.get('grapheme_index')
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
        valid.append({**event, 'name': name, 'start_ms': start})
    valid.sort(key=lambda e: e['start_ms'])
    out, used = [], set()
    for phrase in phrases or []:
        text = str(phrase.get('text') or '')
        if not text:
            continue
        ps, pe = int(phrase.get('start_ms') or 0), int(phrase.get('end_ms') or 0)
        marks, unplaced = [], []
        # A lyric line also needs the chord already sounding at its start.
        # This is a continuation, not an invented new change. N stops carry-in.
        previous = [e for e in events or [] if int(e.get('start_ms',0)) < ps]
        active = max(previous, key=lambda e: int(e.get('start_ms',0)), default=None)
        if active and clean_chord(active.get('name')) and (active.get('end_ms') is None or active['end_ms'] > ps):
            marks.append({**active, 'char_index':0, 'anchor_quality':'continuation', 'continued':True})
        for i, event in enumerate(valid):
            if not ps <= event['start_ms'] < pe:
                continue
            used.add(i)
            at, quality = _anchor(event, {**phrase, 'text': text})
            mark = {**event, 'char_index': at, 'anchor_quality': quality}
            (marks if at is not None else unplaced).append(mark)
        grouped = {}
        for mark in marks:
            grouped.setdefault(mark['char_index'], []).append(mark['name'])
        positions = sorted(set([0, *grouped]))
        cells = [{'text': text[pos:(positions[j+1] if j+1 < len(positions) else len(text))],
                  'chords': grouped.get(pos, []), 'char_index': pos} for j,pos in enumerate(positions)]
        out.append({**phrase, 'cells': cells, 'chords': marks, 'unplaced_chords': unplaced,
                    'timing_quality': 'estimated' if any(m.get('anchor_quality','').startswith('estimated') for m in marks) else 'anchored' if marks and not unplaced else 'line'})
    # Смены в проигрыше/паузе тоже нужны: не теряем их из-за отсутствия текста.
    for i,event in enumerate(valid):
        if i not in used:
            out.append({'text': '', 'start_ms': event['start_ms'], 'end_ms': event['start_ms'],
                        'cells': [], 'chords': [], 'unplaced_chords': [event], 'timing_quality': 'gap'})
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
