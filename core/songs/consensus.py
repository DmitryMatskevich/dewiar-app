"""Conservative word agreement: disagreement is evidence for review, not truth.

The timed recognizer supplies the display spine. Equal-length substitutions
can receive votes; insertions/deletions never borrow invented word times.
Published lyrics are deliberately excluded from this ASR vote.
"""
import copy
import difflib
import re


def normalized(text):
    return re.sub(r'[^\w]+', '', str(text).casefold().replace('ё', 'е'))


def tokens(phrases):
    return [m.group() for p in phrases for m in re.finditer(r'\S+', p.get('text', ''))]


def choose_spine(hypotheses):
    available = [h for h in hypotheses if h.get('phrases')]
    if not available:
        return None
    timed = next((h for h in available if h['model'].startswith('Whisper')), available[0])
    baseline = next((h for h in available if 'gigaam' in h['model'].lower()), None)
    if baseline and baseline is not timed:
        for p in baseline['phrases']:
            words = {normalized(w) for w in tokens([p])} - {''}
            def nearby(h):
                return [q for q in h['phrases'] if q.get('start_ms',0) < p['end_ms'] and q.get('end_ms',0) > p['start_ms']]
            covered = words & {normalized(w) for w in tokens(nearby(timed))}
            if len(words) >= 6 and len(covered) <= len(words)/4:
                # A third model must support the missing content; do not fill
                # instrumental gaps from a single recognizer's hallucinations.
                if any(len(words & {normalized(w) for w in tokens(nearby(h))}) >= len(words)*.5
                       for h in available if h is not baseline and h is not timed):
                    return baseline
    return timed


def consensus(phrases, hypotheses, choose_majority=False):
    output = copy.deepcopy(phrases)
    base = tokens(output)
    evidence = [[word] for word in base]
    # One vote per independent model, regardless of input variants/chunks.
    seen = set()
    for hyp in hypotheses:
        model = hyp.get('model')
        if not model or model in seen:
            continue
        seen.add(model)
        alternative = tokens(hyp.get('phrases', []))
        align = difflib.SequenceMatcher(None, list(map(normalized, base)),
                                       list(map(normalized, alternative)), autojunk=False)
        for tag, a, b, c, d in align.get_opcodes():
            if tag == 'equal' or (tag == 'replace' and b-a == d-c):
                for i, word in zip(range(a, b), alternative[c:d]):
                    evidence[i].append(word)
    cursor = 0
    for phrase in output:
        agreement, pieces, previous_end, changed = [], [], 0, False
        for match in re.finditer(r'\S+', phrase.get('text', '')):
            choices = evidence[cursor]
            counts = {normalized(w):sum(normalized(v)==normalized(w) for v in choices) for w in choices}
            winner = max(counts,key=counts.get)
            value = match.group()
            if choose_majority and winner and counts[winner] >= 2 and counts[winner] > counts[normalized(value)]:
                replacement = next(w for w in choices if normalized(w)==winner)
                value = re.sub(r'\w[\s\S]*\w|\w',lambda _:re.sub(r'^\W+|\W+$','',replacement),value,count=1)
                changed = True
            pieces.append(phrase['text'][previous_end:match.start()])
            offset = sum(len(piece) for piece in pieces)
            pieces.append(value)
            count = counts[normalized(value)]
            agreement.append({'char_start': offset, 'char_end': offset+len(value),
                              'text': value, 'agree': count, 'models': 1+len(seen),
                              'alternatives': list(dict.fromkeys(choices)),
                              'needs_review': count < 2})
            previous_end = match.end()
            cursor += 1
        pieces.append(phrase.get('text','')[previous_end:])
        if changed:
            phrase.update(text=''.join(pieces),words=[],alignment_stale=True,timing_quality='phrase',
                          consensus_changed=True)
        phrase['agreement'] = agreement
        phrase['review_state'] = 'needs_review'
    return output


def phrases_from_words(segments, run_id, max_words=12):
    """Split by measured pauses, punctuation, and readable phrase length."""
    phrases, pending = [], []

    def flush():
        if not pending:
            return
        text, words = '', []
        for word in pending:
            value = str(word['word']).strip()
            if text:
                text += ' '
            offset = len(text)
            text += value
            words.append({'text': value, 'char_start': offset, 'char_end': len(text),
                          'start_ms': round(word['start']*1000), 'end_ms': round(word['end']*1000),
                          'method': 'whisper_attention', 'verified': False})
        phrases.append({'phrase_id': f'{run_id}-{len(phrases)}', 'text': text,
                        'start_ms': words[0]['start_ms'], 'end_ms': words[-1]['end_ms'],
                        'words': words, 'timing_quality': 'word_estimated',
                        'kind': 'unknown', 'note': '', 'review_state': 'needs_review'})
        pending.clear()

    for segment in segments:
        # Attention timestamps sometimes omit most of a sung segment. Never
        # let a timing failure silently delete words from the transcription.
        timed_words = [w for w in segment.get('words', []) if w['end'] > w['start']]
        source_text = str(segment.get('text', '')).strip()
        timed_text = ' '.join(str(w.get('word', '')) for w in timed_words)
        if source_text and normalized(source_text) != normalized(timed_text):
            flush()
            phrases.append({'phrase_id': f'{run_id}-{len(phrases)}', 'text': source_text,
                'start_ms': round(segment['start']*1000), 'end_ms': round(segment['end']*1000),
                'words': [], 'timing_quality': 'phrase', 'kind': 'unknown', 'note': '',
                'review_state': 'needs_review', 'alignment_warning': 'Неполные времена слов; сохранён весь текст сегмента'})
            continue
        for word in segment.get('words', []):
            if not str(word.get('word', '')).strip() or word['end'] <= word['start']:
                continue
            if pending and (word['start']-pending[-1]['end'] > .65 or len(pending) >= max_words):
                flush()
            pending.append(word)
            if str(word['word']).rstrip().endswith(('.', '!', '?', ';')):
                flush()
    flush()
    return phrases


def recover_omissions(phrases, hypotheses):
    """Insert locally corroborated spans without deleting the chosen transcript.

    Segment bounds are search windows, not fabricated word timestamps. A second
    independent recognizer must support each insertion in the same audio window.
    """
    output = copy.deepcopy(phrases)
    baseline = next((h for h in hypotheses if 'gigaam' in h.get('model','').lower()), None)
    if not baseline:
        return output
    def near(rows, p):
        return [q for q in rows if q.get('start_ms',0) < p['end_ms']+1500
                and q.get('end_ms',0) > p['start_ms']-1500]
    def scan(rows):
        return [(normalized(m.group()), q, m.start(), m.end()) for q in rows
                for m in re.finditer(r'\S+', q.get('text',''))]
    for source in baseline.get('phrases',[]):
        local = scan(near(output, source))
        alt = scan([source])
        a, b = [t[0] for t in local], [t[0] for t in alt]
        operations = difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes()
        # Reverse order keeps character offsets valid for multiple insertions.
        for tag, i, j, c, d in reversed(operations):
            if tag != 'insert' or d-c < 3:
                continue
            missing = b[c:d]
            support = []
            for h in hypotheses:
                if h.get('model') == baseline.get('model') or h.get('model','').startswith('Whisper'):
                    continue
                peer = [t[0] for t in scan(near(h.get('phrases',[]),source))]
                matched = sum(x.size for x in difflib.SequenceMatcher(None,missing,peer,autojunk=False).get_matching_blocks())
                if matched >= max(3,len(missing)*.6):
                    support.append(h['model'])
            if not support:
                continue
            text = source['text'][alt[c][2]:alt[d-1][3]]
            prev = local[i-1] if i else None
            nxt = local[i] if i < len(local) else None
            evidence = {'models': sorted(set([baseline['model'],*support])),
                        'text':text, 'start_ms':source['start_ms'], 'end_ms':source['end_ms']}
            if prev and nxt and prev[1] is nxt[1]:
                q = nxt[1]; pos = nxt[2]
                q['text'] = q['text'][:pos]+text+' '+q['text'][pos:]
                q.update(words=[],agreement=[],alignment_stale=True,timing_quality='phrase',review_state='needs_review')
                q.setdefault('recovered_spans',[]).append(evidence)
            else:
                start = max(source['start_ms'],prev[1]['end_ms'] if prev else source['start_ms'])
                end = min(source['end_ms'],nxt[1]['start_ms'] if nxt else source['end_ms'])
                if end <= start:
                    start,end = source['start_ms'],source['end_ms']
                q = dict(text=text,start_ms=start,end_ms=end,words=[],timing_quality='phrase',
                    phrase_id=f"coverage-{source.get('phrase_id','segment')}-{c}-{d}",
                    review_state='needs_review',alignment_stale=True,recovered_spans=[evidence],
                    alignment_warning='Фраза восстановлена по двум распознавателям; время требует проверки')
                index = next((k for k,p in enumerate(output) if nxt and p is nxt[1]),len(output))
                output.insert(index,q)
    return output
