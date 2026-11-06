"""Time-local ASR disagreement and audio-confirmed repair.

Recognition is evidence, not an oracle. Two fresh independent decoders must
agree on a contextual candidate before a variable-length edit is applied.
Forced alignment is deliberately not used as evidence of lexical correctness.
"""
import copy
import difflib
import re

from .consensus import normalized


def family(model):
    name = str(model).lower()
    return next((key for key in ('whisper', 'qwen', 'gigaam') if key in name), name)


def scan(phrases):
    return [dict(text=m.group(), norm=normalized(m.group()), phrase=i,
                 char_start=m.start(), char_end=m.end(),
                 start_ms=p.get('start_ms', 0), end_ms=p.get('end_ms', 0))
            for i, p in enumerate(phrases) for m in re.finditer(r'\S+', p.get('text', ''))]


def timed_edits(a, b):
    """Levenshtein alignment with time gates so repeated choruses cannot vote.

    Phrase bounds only gate comparisons; they never become word timestamps.
    """
    n, m = len(a), len(b)
    costs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        costs[i][0] = i
    for j in range(m + 1):
        costs[0][j] = j
    def match(i, j):
        return (a[i]['norm'] == b[j]['norm'] and
                a[i]['start_ms'] < b[j]['end_ms'] + 1500 and
                b[j]['start_ms'] < a[i]['end_ms'] + 1500)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            costs[i][j] = min(costs[i-1][j] + 1, costs[i][j-1] + 1,
                              costs[i-1][j-1] + (0 if match(i-1, j-1) else 1))
    steps, i, j = [], n, m
    while i or j:
        if i and j and costs[i][j] == costs[i-1][j-1] + (0 if match(i-1, j-1) else 1):
            steps.append(('equal' if match(i-1, j-1) else 'replace', i-1, i, j-1, j))
            i, j = i-1, j-1
        elif i and costs[i][j] == costs[i-1][j] + 1:
            steps.append(('replace', i-1, i, j, j)); i -= 1
        else:
            steps.append(('replace', i, i, j-1, j)); j -= 1
    blocks = []
    for tag, i, end, j, stop in reversed(steps):
        if blocks and blocks[-1][0] == tag:
            blocks[-1][2], blocks[-1][4] = end, stop
        else:
            blocks.append([tag, i, end, j, stop])
    return blocks


def propose_repairs(phrases, hypotheses):
    base = scan(phrases)
    proposals, seen = [], set()
    for hyp in hypotheses:
        alt = scan(hyp.get('phrases', []))
        # A whole sung opening may be absent in the display spine. Keep such
        # claims separate from anchored edits: only a new two-model agreement
        # over the original audio can replace the entire covered block.
        for source in hyp.get('phrases', []):
            start, end = source.get('start_ms', 0), source.get('end_ms', 0)
            source_words = [t['norm'] for t in scan([source])]
            local = [t for t in base if t['start_ms'] < end and t['end_ms'] > start]
            matched = sum(b.size for b in difflib.SequenceMatcher(None,
                source_words, [t['norm'] for t in local], autojunk=False).get_matching_blocks())
            if len(source_words) < 6 or matched >= len(source_words)*.5:
                continue
            start, end = max(0, start-2000), end+2000
            if end-start > 30000:
                continue
            indices = [i for i,t in enumerate(base) if t['start_ms'] < end and t['end_ms'] > start]
            if not indices or any(base[i]['start_ms'] < start or base[i]['end_ms'] > end for i in indices):
                continue
            key=('coverage',indices[0],indices[-1]+1)
            if key in seen:
                continue
            seen.add(key)
            proposals.append(dict(begin=indices[0],end=indices[-1]+1,text='',context=[],
                start_ms=start,end_ms=end,source_model=hyp['model'],status='needs_review',coverage=True))
        blocks = timed_edits(base, alt)
        for k, (tag, i, j, c, d) in enumerate(blocks):
            # Retain the original on deletions and unanchored intro/outro claims.
            if tag == 'equal' or c == d or not k or k+1 == len(blocks):
                continue
            left, right = blocks[k-1], blocks[k+1]
            if min(left[2]-left[1], right[2]-right[1]) < 2:
                continue
            if max(j-i, d-c) > 10 or j-i > 2*(d-c):
                continue
            context = alt[c-2:d+2]
            start = max(0, min(t['start_ms'] for t in context)-2000)
            end = max(t['end_ms'] for t in context)+2000
            # A coarse chunk may cover two lyric lines; cap audio cost elsewhere.
            if end-start > 34000:
                continue
            key = (i, j, tuple(t['norm'] for t in alt[c:d]))
            if key in seen:
                continue
            seen.add(key)
            proposals.append(dict(begin=i, end=j, text=' '.join(t['text'] for t in alt[c:d]),
                context=[t['norm'] for t in context], start_ms=start, end_ms=end,
                source_model=hyp['model'], status='needs_review'))
    # Missing words take priority over punctuation/substitution disagreements.
    return sorted(proposals, key=lambda p: (not p.get('coverage',False), -(len(p['text'].split()) > p['end']-p['begin']),
        p['end_ms']-p['start_ms'], p['start_ms']))


def confirms(proposal, hypothesis):
    words = [t['norm'] for t in scan(hypothesis.get('phrases', []))
             if t['start_ms'] < proposal['end_ms'] and t['end_ms'] > proposal['start_ms']]
    needle = proposal['context']
    if not needle:
        return False
    return any(words[i:i+len(needle)] == needle for i in range(len(words)-len(needle)+1))


def fresh_agreements(phrases, rechecks):
    """New contextual words may emerge only when both ASRs reread a crop.

    Keep confirmations local to one audio window. A failed first-pass spelling
    must not be the only candidate that a second pass is allowed to confirm.
    Unanchored crop edges and whole-block guesses remain review-only.
    """
    proposals=[];seen=set()
    for check in rechecks:
        evidence=check.get('hypotheses',[])
        if len({family(h['model']) for h in evidence})<2:continue
        for p in propose_repairs(phrases,evidence):
            if p.get('coverage'):continue
            models=[h['model'] for h in evidence if confirms(p,h)]
            if len({family(m) for m in models})<2:continue
            key=(p['begin'],p['end'],tuple(p['context']))
            if key in seen:continue
            seen.add(key)
            p.update(confirmed_models=models,discovered_on_recheck=True,
                     evidence_start_ms=check['start_ms'],evidence_end_ms=check['end_ms'])
            proposals.append(p)
    return proposals


def boundary_windows(hypotheses):
    """Check chunk seams and recording edges even when both texts look fluent."""
    rows=[p for h in hypotheses for p in h.get('phrases',[])]
    end=max((p.get('end_ms',0) for p in rows),default=0)
    if end<=0:return []
    windows=[(0,min(16000,end)),(max(0,end-16000),end)]
    for h in hypotheses:
        if family(h.get('model'))!='qwen':continue
        for p in h.get('phrases',[]):
            cut=p.get('start_ms',0)
            if 0<cut<end:windows.append((max(0,cut-8000),min(end,cut+8000)))
    return list(dict.fromkeys(windows))


def quarantine_credit_phrases(phrases, hypotheses, rechecks):
    """Keep unsupported subtitle-credit templates out of the automatic sheet.

    This is a review quarantine, not deletion of recognition evidence or a
    declaration that the interval is silent. Require an independent reread at
    the interval; genuine spoken credits corroborated by another ASR survive.
    Ordinary lyrics containing the word 'редактор' are not a credit template.
    """
    kept, quarantine = [], []
    for p in phrases:
        text=p.get('text','').strip()
        template=re.match(r'^(?:субтитры\s+(?:добавил[аи]?|подготовил[аи]?|создал[аи]?|сделал[аи]?)'
                          r'|редактор\s+субтитров|корректор\s+[А-ЯЁ]\.?)\b',text,re.I)
        if not template or len(text.split())>16 or len(text)>200:
            kept.append(p);continue
        start,end=p.get('start_ms',0),p.get('end_ms',0)
        needle=normalized(text)
        def supports(h):
            local=' '.join(q.get('text','') for q in h.get('phrases',[])
                          if q.get('start_ms',0)<end and q.get('end_ms',0)>start)
            return needle in normalized(local)
        origins={family(h['model']) for h in hypotheses if supports(h)}
        if not origins or len(origins)>1:
            kept.append(p);continue
        rereads=[h for c in rechecks if c['start_ms']<=(start+end)/2<c['end_ms']
                 for h in c.get('hypotheses',[]) if family(h['model']) not in origins]
        if not rereads or any(supports(h) for h in rereads):
            kept.append(p);continue
        quarantine.append(dict(phrase=copy.deepcopy(p),reason='possible_asr_credit',
            label='Возможная служебная вставка о титрах: сохранена отдельно для проверки по записи',
            checked_models=sorted({h['model'] for h in rereads}),review_state='needs_review'))
    return kept,quarantine


def protect_prior_agreement(base, hypotheses, proposals):
    """A fresh crop must not silently overrule two agreeing full recordings.

    A different cut can make both decoders hallucinate the same new word.
    Such 2-versus-2 evidence is a review conflict, not a confident correction.
    Positive insertions remain eligible to recover a jointly omitted seam.
    """
    original=scan(base['phrases']);supported=[]
    for h in hypotheses:
        if family(h['model'])==family(base['model']):continue
        supported.extend((a,b) for tag,a,b,_,_ in timed_edits(original,scan(h['phrases'])) if tag=='equal')
    for p in proposals:
        if p['end']<=p['begin'] or not p.get('confirmed_models'):continue
        retained=iter(normalized(w) for w in p.get('text','').split())
        if all(any(t['norm']==word for word in retained) for t in original[p['begin']:p['end']]):
            continue  # This candidate only adds text; the agreed words survive.
        if any(a<=p['begin']<p['end']<=b for a,b in supported):
            p['recheck_models']=p.pop('confirmed_models')
            p['status']='conflicting_audio_evidence'
            p['reason']='Полная запись и повторное окно дают разные согласованные варианты; сохранён исходный текст'


def confirm_coverage(proposal, evidence):
    if len({family(h['model']) for h in evidence}) < 2:
        return
    a, b = [scan(h.get('phrases', [])) for h in evidence[:2]]
    matched = sum(block.size for block in difflib.SequenceMatcher(None,
        [t['norm'] for t in a], [t['norm'] for t in b], autojunk=False).get_matching_blocks())
    if matched < 6 or matched/max(len(a),len(b),1) < .7:
        return
    # Retain full text from the longer supported candidate; never intersect
    # token sets (that operation was the original source of silent word loss).
    chosen = max((a,b),key=len)
    proposal.update(text=' '.join(t['text'] for t in chosen),
                    confirmed_models=[h['model'] for h in evidence[:2]],
                    audio_start_ms=min(t['start_ms'] for t in chosen),
                    audio_end_ms=max(t['end_ms'] for t in chosen))


def apply_repairs(phrases, proposals):
    """Apply confirmed edits backwards; merge crossed phrases without lost tails."""
    output = copy.deepcopy(phrases)
    original = scan(phrases)
    accepted = []
    for p in proposals:
        if len({family(m) for m in p.get('confirmed_models', [])}) < 2:
            continue
        i, j = p['begin'], p['end']
        # Conflict only on edited tokens, not an entire 25-second phrase.
        if any(i < max(q['end'],q['begin']+1) and q['begin'] < max(j,i+1) for q in accepted):
            continue
        accepted.append(p)
    groups = []
    for p in sorted(accepted,key=lambda p:p['begin']):
        lo=original[p['begin']]['phrase']; hi=original[max(p['begin'],p['end']-1)]['phrase']
        if groups and lo <= groups[-1][1]:
            groups[-1][1]=max(hi,groups[-1][1]);groups[-1][2].append(p)
        else:
            groups.append([lo,hi,[p]])
    for lo,hi,edits in reversed(groups):
        offsets={}; parts=[]; position=0
        for index in range(lo,hi+1):
            offsets[index]=position;parts.append(phrases[index]['text'])
            position+=len(parts[-1])+1
        text=' '.join(parts)
        for p in reversed(edits):
            first=original[p['begin']];last=original[max(p['begin'],p['end']-1)]
            start=offsets[first['phrase']]+first['char_start']
            stop=(offsets[last['phrase']]+last['char_end']) if p['end']>p['begin'] else start
            replacement=p['text']+(' ' if p['begin']==p['end'] else '')
            text=text[:start]+replacement+text[stop:]
        q = copy.deepcopy(output[lo])
        q.update(text=text,
                 end_ms=output[hi]['end_ms'], words=[], agreement=[],
                 timing_quality='phrase', alignment_stale=True, review_state='needs_review')
        for p in edits:
            if p.get('coverage'):
                q.update(start_ms=p['audio_start_ms'],end_ms=p['audio_end_ms'])
            q.setdefault('audio_repairs', []).append(dict(p, status='two_model_audio_agreement'))
            p['status'] = 'two_model_audio_agreement'
        output[lo:hi+1] = [q]
    return output
