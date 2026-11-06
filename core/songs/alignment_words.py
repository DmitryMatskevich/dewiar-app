"""Map aligner units to unchanged lyric tokens without inventing word times."""
import math
import re
from .consensus import normalized


def measured_words(text, units, start_ms, end_ms):
    matches=[m for m in re.finditer(r'\S+',text) if normalized(m.group())]
    spans=[];cursor=0
    for unit in units:
        token=normalized(unit.text)
        if not token:continue
        spans.append((cursor,cursor+len(token),unit));cursor+=len(token)
    if ''.join(normalized(m.group()) for m in matches)!=''.join(normalized(s[2].text) for s in spans):
        return []
    words=[];cursor=0
    for match in matches:
        stop=cursor+len(normalized(match.group()))
        group=[s for s in spans if s[0]<stop and s[1]>cursor]
        # Joining syllables is measured; splitting one timestamp across two
        # source words would be an invented timing and is intentionally skipped.
        if group and group[0][0]==cursor and group[-1][1]==stop:
            times=[(start_ms+u.start_time*1000,start_ms+u.end_time*1000) for _,_,u in group]
            valid=all(math.isfinite(a) and math.isfinite(b) and start_ms<=a<b<=end_ms for a,b in times)
            valid=valid and all(a[1]<=b[0] for a,b in zip(times,times[1:]))
            if valid:
                a,b=round(times[0][0]),round(times[-1][1])
                if b>a and (not words or a>=words[-1]['end_ms']):
                    words.append(dict(text=match.group(),char_start=match.start(),char_end=match.end(),
                                      start_ms=a,end_ms=b,method='qwen3_forced_aligner',verified=False))
        cursor=stop
    return words
