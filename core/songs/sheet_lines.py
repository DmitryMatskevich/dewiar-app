"""Presentation-only lyric lines; preserve original phrases and manual history."""
import re


def readable_sheet(rows, max_chars=64, section_starts=()):
    output, group = [], []
    starts = set(section_starts)

    def flush():
        if not group:
            return
        text, marks, spans = '', [], []
        for row in group:
            if text and not text[-1].isspace():
                text += ' '
            offset = len(text)
            text += row['text']
            spans.append((offset,len(text),row))
            # The full sheet already shows the original change (including intro/gap rows).
            # Carry-in marks belong to isolated phrase previews, not new changes.
            marks.extend(dict(m,char_index=offset+m['char_index']) for m in row.get('chords',[])
                         if m.get('char_index') is not None and not m.get('continued'))
        # Preserve explicit line breaks; otherwise use punctuation and word limits.
        words = list(re.finditer(r'\S+',text))
        bounds, begin = [0],0
        for i,w in enumerate(words):
            if i+1 == len(words):
                break
            following = words[i+1]
            value = w.group()
            hard = '\n' in text[w.end():following.start()]
            punctuation = bool(re.search(r'[.!?;][»”"\)]?$',value))
            verse = value.endswith(',') and following.group()[0].isupper() and w.end()-begin>=18
            long = following.end()-begin > max_chars
            if hard or punctuation or verse or long:
                cut = following.start()
                if long and not (hard or punctuation or verse) and value.casefold() in {'не','ни','и','а','но','в','на','с','к','по','без','для','о','из','от'} and w.start()>begin:
                    cut = w.start()
                if cut > bounds[-1]:
                    bounds.append(cut);begin=cut
        bounds.append(len(text))
        shown = set()
        for a,b in zip(bounds,bounds[1:]):
            # Do not leave a hanging preposition/conjunction or one-word shard.
            content = text[a:b].rstrip()
            if not content:
                continue
            owners = [r for lo,hi,r in spans if lo < b and hi > a]
            source = owners[0]
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
                'unplaced_chords':[], 'display_only':True,'source_phrase_ids':[r.get('phrase_id') for r in owners]})
        group.clear()

    for row in rows:
        if not row.get('text'):
            flush();output.append(row);continue
        if group and (row.get('start_ms',0)-group[-1].get('end_ms',0)>2500 or row.get('phrase_id') in starts):
            flush()
        group.append(row)
    flush()
    return output
