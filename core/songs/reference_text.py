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
    current = []
    for p in phrases:
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
        for n, (tag, i, j, c, d) in enumerate(blocks):
            if tag != 'replace' or max(j-i, d-c) > 8 or not n or n+1 == len(blocks):
                continue
            before, after = blocks[n-1], blocks[n+1]
            if before[0] != 'equal' or after[0] != 'equal' or min(before[2]-before[1], after[2]-after[1]) < 2:
                continue
            target = current[i:j]
            if any(t['phrase'] is not target[0]['phrase'] for t in target):
                continue
            left = ''.join(t['norm'] for t in target)
            right = ''.join(t['norm'] for t in pub[c:d])
            similarity = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
            if similarity < .7:
                continue
            one = _fragment(target[0]['phrase'], target)
            two = _fragment(dict(text=ref['plain_lyrics'], origin='publication',
                                 url=ref.get('url'), version_verified=ref.get('version_verified')), pub[c:d])
            key = (one['phrase_id'], one['char_start'], one['char_end'],
                   tuple(t['norm'] for t in pub[c:d]))
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(current=one, alternative=two, status='needs_target_audio_check',
                            character_similarity=round(similarity, 3), auto_apply=False))
    return out
