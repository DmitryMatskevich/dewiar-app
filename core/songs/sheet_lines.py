"""Presentation-only lyric lines; preserve original phrases and manual history."""
import re
from .playback_layout import display_words


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


def readable_sheet(rows, max_chars=42, section_starts=()):
    output, group = [], []
    starts = set(section_starts)

    def flush():
        if not group:
            return
        text, marks, spans, cues = '', [], [], []
        for row in group:
            if text and not text[-1].isspace():
                text += ' '
            offset = len(text)
            text += row['text']
            spans.append((offset,len(text),row))
            cues.extend(dict(w, phrase_id=row.get('phrase_id'),
                             phrase_char_start=w['char_start'],
                             char_start=offset+w['char_start'], char_end=offset+w['char_end'])
                        for w in display_words(row))
            # The full sheet already shows the original change (including intro/gap rows).
            # Carry-in marks belong to isolated phrase previews, not new changes.
            marks.extend(dict(m,char_index=offset+m['char_index']) for m in row.get('chords',[])
                         if m.get('char_index') is not None and not m.get('continued'))
        # Preserve explicit line breaks; otherwise use punctuation and word limits.
        words = list(re.finditer(r'\S+',text))
        bounds, begin, soft = [0],0,set()
        for i,w in enumerate(words):
            if i+1 == len(words):
                break
            following = words[i+1]
            value = w.group()
            hard = '\n' in text[w.end():following.start()]
            punctuation = bool(re.search(r'[.!?;][»”"\)]?$',value))
            verse = following.group()[0].isupper() and w.end()-begin>=18 and not value.endswith((':','—','-'))
            long = following.end()-begin > max_chars
            if hard or punctuation or verse or long:
                cut = following.start()
                if long and not (hard or punctuation or verse) and value.casefold() in {'не','ни','и','а','но','в','на','с','к','по','без','для','о','из','от'} and w.start()>begin:
                    cut = w.start()
                if cut > bounds[-1]:
                    bounds.append(cut);begin=cut
                    if long and not (hard or punctuation or verse):
                        soft.add(cut)
        bounds.append(len(text))
        bounds = _balance_soft_wraps(text, bounds, soft, max_chars)
        shown = set()
        for a,b in zip(bounds,bounds[1:]):
            # Do not leave a hanging preposition/conjunction or one-word shard.
            content = text[a:b].rstrip()
            if not content:
                continue
            owners = [r for lo,hi,r in spans if lo < b and hi > a]
            source = owners[0]
            source_spans = []
            for lo, hi, owner in spans:
                left, right = max(lo, a), min(hi, a + len(content))
                if left >= right:
                    continue
                source_spans.append({
                    'phrase_id': owner.get('phrase_id'),
                    'phrase_char_start': left - lo,
                    'phrase_char_end': right - lo,
                    'display_char_start': left - a,
                    'display_char_end': right - a,
                })
            chosen = [dict(m,char_index=m['char_index']-a) for m in marks if a<=m['char_index']<a+len(content)]
            grouped = {}
            for m in chosen:
                grouped.setdefault(m['char_index'],[]).append(m['name'])
            positions=sorted({0,*grouped})
            cells=[{'char_index':pos,'text':content[pos:positions[j+1] if j+1<len(positions) else len(content)],
                    'chords':list(dict.fromkeys(grouped.get(pos,[])))} for j,pos in enumerate(positions)]
            pid=source.get('phrase_id')
            display_id=pid if pid not in shown else f'{pid}:line:{a}'
            shown.add(pid)
            output.append({**source,'phrase_id':display_id,'text':content,'cells':cells,'chords':chosen,
                'unplaced_chords':[], 'display_only':True,'source_phrase_ids':[r.get('phrase_id') for r in owners],
                'source_spans':source_spans,
                'display_words':[dict(w,char_start=w['char_start']-a,char_end=w['char_end']-a)
                                 for w in cues if a<=w['char_start'] and w['char_end']<=a+len(content)]})
        group.clear()

    for row in rows:
        if not row.get('text'):
            flush();output.append(row);continue
        if group and (row.get('start_ms',0)-group[-1].get('end_ms',0)>2500 or row.get('phrase_id') in starts):
            flush()
        group.append(row)
    flush()
    return output
