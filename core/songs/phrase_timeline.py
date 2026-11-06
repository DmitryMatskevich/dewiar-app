"""Repair omission ordering from ASR provenance, never from a lyric guess."""
import copy
import re
from .consensus import normalized


def _tokens(text):
    return [normalized(w) for w in re.findall(r'\S+', str(text or '')) if normalized(w)]


def _legacy_links(phrase, rows, hypotheses):
    """Old coverage records have a source window but no neighbor IDs."""
    links = []
    for span in phrase.get('recovered_spans', []):
        needle = _tokens(span.get('text'))
        if len(needle) < 3:
            continue
        for h in hypotheses:
            if h.get('model') not in span.get('models', []):
                continue
            for source in h.get('phrases', []):
                if (source.get('start_ms'), source.get('end_ms')) != (span.get('start_ms'), span.get('end_ms')):
                    continue
                words = _tokens(source.get('text'))
                positions = [i for i in range(len(words)-len(needle)+1) if words[i:i+len(needle)] == needle]
                if len(positions) != 1:
                    continue
                i = positions[0]
                prefix, suffix = words[:i], words[i+len(needle):]
                near = [p for p in rows if p is not phrase and
                        p.get('start_ms',0) < span['end_ms']+1500 and
                        p.get('end_ms',0) > span['start_ms']-1500]
                def matches(part, after):
                    size = min(4,len(part))
                    if size < 2:
                        return []
                    return [p['phrase_id'] for p in near if
                            (_tokens(p.get('text'))[-size:] == part[-size:] if after else
                             _tokens(p.get('text'))[:size] == part[:size])]
                previous, following = matches(prefix,True), matches(suffix,False)
                if len(previous) == 1 or len(following) == 1:
                    links.append({'after_phrase_id':previous[0] if len(previous)==1 else None,
                                  'before_phrase_id':following[0] if len(following)==1 else None})
    return links[0] if links and all(link==links[0] for link in links) else None


def ordered_phrases(phrases, hypotheses=()):
    """Keep all IDs/text/edits, relocate only explicitly supported recovered spans.

    Broad segment times are search windows, not permission to put a suffix
    before its measured prefix. Any clamped timing remains explicitly estimated.
    """
    rows = copy.deepcopy(phrases)
    for p in rows:
        if p.get('recovered_spans') and not p.get('recovery_order'):
            link = _legacy_links(p,rows,hypotheses)
            if link:
                p['recovery_order'] = link
    # A second pass allows adjacent recovered spans to resolve their neighbors.
    for _ in range(2):
        for p in list(rows):
            link = p.get('recovery_order') or {}
            previous = next((q for q in rows if q is not p and q.get('phrase_id')==link.get('after_phrase_id')),None)
            following = next((q for q in rows if q is not p and q.get('phrase_id')==link.get('before_phrase_id')),None)
            if not previous and not following:
                continue
            if previous and following and rows.index(previous) >= rows.index(following):
                continue  # conflicting provenance is not an ordering instruction
            rows.remove(p)
            index = rows.index(previous)+1 if previous else rows.index(following)
            rows.insert(index,p)
    for i,p in enumerate(rows):
        if not p.get('recovery_order') or (p.get('words') and not p.get('alignment_stale')):
            continue
        a,b = p.get('start_ms',0),p.get('end_ms',0)
        start = max(a,rows[i-1].get('end_ms',a)) if i else a
        end = min(b,rows[i+1].get('start_ms',b)) if i+1<len(rows) else b
        if start < end and (start,end)!=(a,b):
            p.setdefault('recovery_window',{'start_ms':a,'end_ms':b})
            p.update(start_ms=start,end_ms=end,alignment_stale=True,timing_quality='phrase')
    return rows


def alignment_groups(phrases):
    """Align a recovered continuation with its neighbor, not across a hard cut."""
    links = set()
    for i,p in enumerate(phrases):
        link = p.get('recovery_order') or {}
        if i and link.get('after_phrase_id') == phrases[i-1].get('phrase_id'):
            links.add(i)
        elif i+1<len(phrases) and link.get('before_phrase_id') == phrases[i+1].get('phrase_id'):
            links.add(i+1)
    groups = []
    for i,p in enumerate(phrases):
        if i not in links:
            groups.append([])
        groups[-1].append(p)
    return groups
