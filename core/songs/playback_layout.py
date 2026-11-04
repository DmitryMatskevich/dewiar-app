"""Display-only word cues; estimated cues never replace alignment data."""
import re


def display_words(phrase):
    tokens = list(re.finditer(r'\S+', phrase.get('text', '')))
    start, end = phrase.get('start_ms', 0), phrase.get('end_ms', 0)
    if not tokens or end <= start:
        return []
    cues = [None] * len(tokens)
    previous_end = start
    if not phrase.get('alignment_stale'):
        for i, token in enumerate(tokens):
            for word in phrase.get('words', []):
                a, b = word.get('start_ms'), word.get('end_ms')
                if (a is not None and b is not None and previous_end <= a < b <= end
                        and word.get('char_start', -1) == token.start()
                        and word.get('char_end', -1) == token.end()):
                    cues[i] = dict(start_ms=a, end_ms=b, quality='aligned')
                    previous_end = b
                    break
    i = 0
    while i < len(tokens):
        if cues[i] is not None:
            i += 1
            continue
        j = i
        while j < len(tokens) and cues[j] is None:
            j += 1
        a = cues[i-1]['end_ms'] if i else start
        b = cues[j]['start_ms'] if j < len(tokens) else end
        weights = [max(1, len(re.findall('[аеёиоуыэюяaeiouy]', t.group(), re.I))) for t in tokens[i:j]]
        total, elapsed = sum(weights), 0
        for k, weight in enumerate(weights, i):
            cues[k] = dict(start_ms=a+(b-a)*elapsed/total,
                           end_ms=a+(b-a)*(elapsed+weight)/total, quality='estimated_phrase')
            elapsed += weight
        i = j
    return [dict(cue, char_start=t.start(), char_end=t.end(),
                 word_id=f"{phrase.get('phrase_id','phrase')}:{t.start()}") for t, cue in zip(tokens, cues)]
