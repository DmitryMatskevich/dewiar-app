"""Read-only text postpass experiment. No model, network, or SongStore mutations.

Use an isolated environment with pymorphy3 for --morphology. Dictionary absence
is a review signal, never proof of an error. Candidate generation is intentionally
separate from audio verification and applying an edit.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import time

TOKEN = re.compile(r"[\w]+(?:[-’'][\w]+)*", re.UNICODE)


def tokens(text):
    return [dict(text=m.group(), norm=m.group().casefold().replace('ё', 'е'),
                 start=m.start(), end=m.end()) for m in TOKEN.finditer(text)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vocalization(word):
    parts = word.lower().split('-')
    return len(parts) > 1 and len(set(parts)) == 1 and parts[0] in {'а', 'о', 'у', 'э', 'ла', 'на'}


def lexical_signals(phrases, known, lexicon=()):
    """Unknown words are not labelled incorrect; proper names and slang survive."""
    allowed = {w.casefold().replace('ё', 'е') for w in lexicon}
    rows = []
    for p in phrases:
        for t in tokens(p.get('text', '')):
            w = t['norm']
            if (w in allowed or vocalization(w) or not re.fullmatch('[а-я-]+', w)
                    or len(w) < 3 or known(w)):
                continue
            rows.append(dict(phrase_id=p.get('phrase_id'), start_ms=p.get('start_ms'),
                             end_ms=p.get('end_ms'), char_start=t['start'], char_end=t['end'],
                             text=t['text'], status='dictionary_unknown_not_error'))
    return rows


def _fragment(item, part):
    return dict(text=item['text'][part[0]['start']:part[-1]['end']],
                phrase_id=item.get('phrase_id'), start_ms=item.get('start_ms'),
                end_ms=item.get('end_ms'), char_start=part[0]['start'], char_end=part[-1]['end'],
                origin=item.get('origin', 'repeat'), url=item.get('url'),
                version_verified=bool(item.get('version_verified')))


def _cleared(item, part):
    """A fragment to delete, with the space (and a trailing mark) that joined it to its line."""
    text = item['text']
    a, b = part[0]['start'], part[-1]['end']
    if not text[b:].strip(' \t,;:—–-'):
        a, b = len(text[:a].rstrip()), len(text)      # the end of a line
    else:
        b = len(text) - len(text[b:].lstrip())          # the start of a line
    return dict(_fragment(item, part), text=text[a:b], char_start=a, char_end=b)


def _current(target, order):
    """The recognized fragment a published one replaces.

    A Qwen chunk boundary (every 25 s) can cut a line, even a word, in two:
    «а может быть при | подавателей в галфака». The publication's words go to
    the line holding most of the fragment; the rest is cleared from its
    neighbour (``also``). Never across more than two neighbouring lines.
    """
    owners = []
    for t in target:
        if not owners or owners[-1] is not t['phrase']:
            owners.append(t['phrase'])
    if len(owners) == 1:
        return _fragment(owners[0], target)
    if len(owners) > 2 or abs(order.get(id(owners[0]), 0) - order.get(id(owners[1]), 0)) != 1:
        return None
    parts = [[t for t in target if t['phrase'] is owner] for owner in owners]
    main = max((0, 1), key=lambda k: sum(len(t['text']) for t in parts[k]))
    if len(parts[1 - main]) == len(tokens(owners[1 - main].get('text', ''))):
        return None  # a whole line, not a cut-off piece of one
    one = _fragment(owners[main], parts[main])
    one['also'] = [_cleared(owners[1 - main], parts[1 - main])]
    one['context'] = ' '.join(t['text'] for t in target)
    return one


def boundary_candidates(phrases, references=(), max_tokens=6000):
    """Find identical letter sequences with different word boundaries.

    Compare local windows of up to eight tokens. Strip shared context and dedupe
    overlapping windows. No preference for the shortest/most common tokenization:
    both directions remain alternatives until the target audio is checked.
    Repeated choruses and copied publications are not independent ASR votes.
    """
    items = [dict(p, origin='repeat') for p in phrases]
    seen_reference = set()
    for ref in references:
        text = ref.get('plain_lyrics', '')
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        if not text or fingerprint in seen_reference:
            continue
        seen_reference.add(fingerprint)
        items.append(dict(text=text, origin='publication', url=ref.get('url'),
                          version_verified=ref.get('version_verified', False)))
    tokenized = [tokens(p.get('text', '')) for p in items]
    if sum(map(len, tokenized)) > max_tokens:
        return dict(status='too_large', candidates=[])
    index = defaultdict(list)
    for owner, ts in enumerate(tokenized):
        for start in range(len(ts)):
            for end in range(start+1, min(start+8, len(ts))+1):
                part = ts[start:end]
                letters = ''.join(t['norm'] for t in part)
                if 8 <= len(letters) <= 64:
                    index[letters].append((owner, start, end))
    found, limited_groups = {}, 0
    for spans in index.values():
        # Keep work bounded on long repeated refrains, with an explicit status.
        if len(spans) > 80:
            limited_groups += 1
            spans = spans[:80]
        for offset, (a, i, j) in enumerate(spans):
            if items[a]['origin'] != 'repeat':
                continue
            for b, c, d in spans[offset+1:]:
                if a == b:
                    continue
                left, right = tokenized[a][i:j], tokenized[b][c:d]
                if [t['norm'] for t in left] == [t['norm'] for t in right]:
                    continue
                while left and right and left[0]['norm'] == right[0]['norm']:
                    left, right = left[1:], right[1:]
                while left and right and left[-1]['norm'] == right[-1]['norm']:
                    left, right = left[:-1], right[:-1]
                if not left or not right:
                    continue
                one, two = _fragment(items[a], left), _fragment(items[b], right)
                # A duplicate web page is evidence once; preserve separate audio
                # occurrences, since each occurrence requires its own check.
                key = (a, one['char_start'], one['char_end'], two['text'].casefold(),
                       two['origin'], two.get('phrase_id'))
                found.setdefault(key, dict(current=one, alternative=two,
                    status='needs_target_audio_check', same_letters=True, auto_apply=False))
    return dict(status='candidates_only', candidates=list(found.values()),
                limited_groups=limited_groups)


def reference_candidates(phrases, references):
    """Short anchored differences, not wholesale substitution of a publication."""
    import difflib
    # Text over silence (credits, «Да.» after the last chord) is not sung and
    # is not compared: it would break the anchor at the end of the song.
    sung = [p for p in phrases if p.get('vocal_evidence') != 'none']
    order = {id(p): k for k, p in enumerate(sung)}
    current = []
    for p in sung:
        current.extend(dict(t, phrase=p) for t in tokens(p.get('text', '')))
    if not current or len(current) > 6000:
        return []
    out, seen = [], set()
    for ref in references:
        pub = tokens(ref.get('plain_lyrics', ''))
        if not pub or len(pub) > 6000:
            continue
        blocks = difflib.SequenceMatcher(None, [t['norm'] for t in current],
                                        [t['norm'] for t in pub], autojunk=False).get_opcodes()

        def anchor(block, start, edges=True):
            # Equal words next to the difference; the start or the end of
            # both texts pins it like two more words («… Мандельштам»).
            if block is None:
                return 2
            if block[0] != 'equal':
                return 0
            edge = edges and ((block[1] == 0 and block[3] == 0) if start else
                              (block[2] == len(current) and block[4] == len(pub)))
            return block[2] - block[1] + (2 if edge else 0)
        for n, (tag, i, j, c, d) in enumerate(blocks):
            if tag != 'replace' or max(j-i, d-c) > 8:
                continue
            target = current[i:j]
            left = ''.join(t['norm'] for t in target)
            right = ''.join(t['norm'] for t in pub[c:d])
            # Only spacing differs («в разнос» / «вразнос»): a publication's
            # habit, it needs two equal words on each side, the text edge
            # does not count.
            edges = left != right
            before = anchor(blocks[n-1] if n else (None if i == 0 and c == 0 and edges else ('x',)), True, edges)
            after = anchor(blocks[n+1] if n+1 < len(blocks) else
                           (None if j == len(current) and d == len(pub) and edges else ('x',)), False, edges)
            if min(before, after) < 2:
                continue
            similarity = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
            # A misheard phrase («Кто повторит?» for «И если не лень, то
            # повторим») shares few letters; firm anchors on both sides (three
            # equal words) still pin the publication's words to this place.
            # A short fragment that sounds alike («Босе, Блядимельич» for
            # «Осип, блять, Эмильич») is proposed from 0.6.
            firm = min(before, after) >= 3 and j - i <= 6
            close = j - i <= 4 and similarity >= .6
            if similarity < .7 and not (firm and similarity >= .4) and not close:
                continue
            one = _current(target, order)
            if one is None:
                continue
            two = _fragment(dict(text=ref['plain_lyrics'], origin='publication',
                                 url=ref.get('url'), version_verified=ref.get('version_verified')), pub[c:d])
            key = (one['phrase_id'], one['char_start'], one['char_end'],
                   tuple(t['norm'] for t in pub[c:d]))
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(current=one, alternative=two, status='needs_target_audio_check',
                            character_similarity=round(similarity, 3), auto_apply=False))
    # A global sequence alignment consumes each published chorus once. Match
    # local lines independently as well, so extra sung repeats retain evidence.
    # These are suggestions only: a matching publication is not an audio vote.
    for ref in references:
        lines=[line.strip() for line in ref.get('plain_lyrics','').splitlines() if line.strip()]
        contexts=list(dict.fromkeys(lines+['\n'.join(lines[i:i+2]) for i in range(len(lines)-1)]))
        for line in contexts:
            pub=tokens(line)
            if not 4<=len(pub)<=18:continue
            pn=[t['norm'] for t in pub]
            for i in range(len(current)):
                if current[i]['norm']!=pn[0] and (len(pn[0])<4 or difflib.SequenceMatcher(None,current[i]['norm'],pn[0]).ratio()<.8):continue
                best=None
                for size in range(max(3,len(pub)-3),min(len(current)-i,len(pub)+3)+1):
                    target=current[i:i+size];tn=[t['norm'] for t in target]
                    matcher=difflib.SequenceMatcher(None,tn,pn,autojunk=False)
                    equal=sum(b.size for b in matcher.get_matching_blocks())
                    ratio=difflib.SequenceMatcher(None,''.join(tn),''.join(pn),autojunk=False).ratio()
                    if equal<2 or ratio<.8:continue
                    score=ratio+equal/max(len(tn),len(pn))*.1
                    if best is None or score>best[0]:best=(score,target,matcher)
                if best is None:continue
                _,target,matcher=best
                for tag,a,b,c,d in matcher.get_opcodes():
                    if tag!='replace' or max(b-a,d-c)>8:continue
                    part=target[a:b]
                    similarity=difflib.SequenceMatcher(None,''.join(t['norm'] for t in part),''.join(pn[c:d]),autojunk=False).ratio()
                    if similarity<.65:continue
                    one=_current(part,order)
                    if one is None:continue
                    two=_fragment(dict(text=line,origin='publication',url=ref.get('url')),pub[c:d])
                    key=(one['phrase_id'],one['char_start'],one['char_end'],tuple(pn[c:d]))
                    if key in seen:continue
                    seen.add(key)
                    out.append(dict(current=one,alternative=two,status='needs_target_audio_check',
                                    character_similarity=round(similarity,3),auto_apply=False,match_method='repeated_line'))
    # Larger anchored replacements can subsume a one-word proposal from the
    # same page. Present one actionable edit, not mutually overlapping boxes.
    result=[]
    for item in sorted(out,key=lambda c:-(c['current']['char_end']-c['current']['char_start'])):
        c=item['current']
        if any(c['phrase_id']==r['current']['phrase_id'] and item['alternative'].get('url')==r['alternative'].get('url')
               and c['char_start']<r['current']['char_end'] and c['char_end']>r['current']['char_start'] for r in result):continue
        result.append(item)
    order={p.get('phrase_id'):i for i,p in enumerate(phrases)}
    return sorted(result,key=lambda c:(order.get(c['current']['phrase_id'],0),c['current']['char_start']))
