"""Recover a clipped opening window from independent lexical/audio evidence.

Coarse ASR segment boundaries are not vocal boundaries. Never infer an intro
from chord changes or stem energy alone, and never replace the selected text.
"""
import math
import re
from .consensus import normalized
from .alignment_words import measured_words


def matching_words(text, decoded):
    tokens = list(re.finditer(r'\S+', text))
    wanted = [normalized(t.group()) for t in tokens]
    words = [w for s in decoded.get('segments', []) for w in s.get('words', [])
             if normalized(w.get('word', ''))]
    observed = [normalized(w['word']) for w in words]
    positions = [i for i in range(len(words)-len(wanted)+1)
                 if observed[i:i+len(wanted)] == wanted]
    if len(wanted) < 3 or len(positions) != 1:
        return []
    selected = words[positions[0]:positions[0]+len(wanted)]
    result = []
    for token, word in zip(tokens, selected):
        a, b = float(word['start'])*1000, float(word['end'])*1000
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b):
            return []
        if result and a < result[-1]['end_ms']:
            return []
        result.append(dict(text=token.group(), char_start=token.start(), char_end=token.end(),
                           start_ms=round(a), end_ms=round(b)))
    return result


def recover_intro(model, samples, sr, vocals, phrase, next_start_ms, decode):
    """Two unprompted Whisper views locate a window; Qwen confirms word times.

    Used only for a failed opening alignment. A repeated/partial lyric or a
    disagreement leaves the original data untouched and records the failure.
    """
    report = dict(applied=False, method='whisper_two_views_qwen_alignment')
    if vocals is None:
        return None, dict(report, reason='no_vocal_stem')
    duration = len(samples)*1000/sr
    limit = min(duration, 60000, next_start_ms+2000 if next_start_ms else 60000)
    vs, vr = vocals
    views = [matching_words(phrase['text'], decode(samples[:round(limit*sr/1000)], sr)),
             matching_words(phrase['text'], decode(vs[:round(limit*vr/1000)], vr))]
    report['views'] = views
    if not all(views) or len(views[0]) != len(views[1]):
        return None, dict(report, reason='text_not_confirmed_in_both_views')
    a, b = views
    if any(abs(x['start_ms']-y['start_ms']) > 400 or
           abs(x['end_ms']-y['end_ms']) > 700 for x, y in zip(a, b)):
        return None, dict(report, reason='unstable_word_times')
    if min(a[0]['start_ms'], b[0]['start_ms']) <= phrase.get('start_ms', 0):
        return None, dict(report, reason='no_confirmed_intro')
    start = max(0, min(a[0]['start_ms'], b[0]['start_ms'])-1200)
    end = min(duration, max(a[-1]['end_ms'], b[-1]['end_ms'])+1000)
    units = list(model.align(audio=(samples[round(start*sr/1000):round(end*sr/1000)], sr),
                             text=phrase['text'], language='Russian')[0])
    measured = measured_words(phrase['text'], units, start, end)
    report['forced_words'] = [dict(w) for w in measured]
    if len(measured) == len(a) and measured[0]['start_ms'] < min(a[0]['start_ms'], b[0]['start_ms'])-400:
        # Forced alignment can stretch even the narrowed first word to the
        # crop boundary. Both unprompted views must agree on its attack AND
        # Qwen must confirm its tail and all subsequent words below.
        measured[0] = dict(measured[0], start_ms=max(a[0]['start_ms'], b[0]['start_ms']),
                           method='whisper_cross_view_onset_qwen_tail')
    if len(measured) != len(a) or any(
            abs(w['start_ms']-v['start_ms']) > (1000 if i == 0 else 500) or
            abs(w['end_ms']-v['end_ms']) > 700
            for i, (w, v) in enumerate(zip(measured, b))):
        return None, dict(report, reason='forced_alignment_disagrees')
    if next_start_ms and measured[-1]['end_ms'] > next_start_ms:
        return None, dict(report, reason='overlaps_next_phrase')
    return measured, dict(report, applied=True, start_ms=measured[0]['start_ms'],
                          end_ms=measured[-1]['end_ms'])


def bounds_with_words(phrase, words):
    """Partial alignments must still enclose every measured word."""
    start, end = int(phrase.get('start_ms') or 0), int(phrase.get('end_ms') or 0)
    if words:
        start = min(start, min(w['start_ms'] for w in words))
        end = max(end, max(w['end_ms'] for w in words))
        if words[0]['char_start'] == 0:
            start = words[0]['start_ms']
        if words[-1]['char_end'] == len(phrase['text']):
            end = words[-1]['end_ms']
    return start, max(start, end)


def repair_opening_insertions(phrases, whisper, qwen):
    """Keep selected words/IDs; accept only timed, two-model missing spans."""
    from .reconciliation import propose_repairs, confirms, apply_repairs
    import copy
    proposals = propose_repairs(phrases, [whisper, qwen])
    accepted = []
    for p in proposals:
        if p.get('coverage') or p['begin'] != p['end'] or not 3 <= len(p['text'].split()) <= 10:
            continue
        if all(confirms(p, h) for h in (whisper, qwen)):
            p['confirmed_models'] = [whisper['model'], qwen['model']]
            accepted.append(p)
    candidate = apply_repairs(phrases, accepted)
    timed = [w for p in whisper.get('phrases',[]) for w in p.get('words',[])]
    decoded = {'segments':[{'words':[dict(word=w['text'],start=w['start_ms']/1000,
                                          end=w['end_ms']/1000) for w in timed]}]}
    originals = {p['phrase_id']:p for p in phrases}
    updated = []
    changes = []
    for p in candidate:
        old = originals.get(p['phrase_id'])
        if old and p['text'] != old['text']:
            words = matching_words(p['text'], decoded)
            if not words or old.get('manual_edited') or old.get('review_state') in ('reviewed','verified'):
                updated.append(copy.deepcopy(old))
                continue
            for w in words:
                w.update(method='whisper_attention',verified=False)
            p.update(words=words,alignment_stale=False,timing_quality='word_estimated',
                     start_ms=words[0]['start_ms'],end_ms=words[-1]['end_ms'])
            changes.append(p['phrase_id'])
        updated.append(p)
    return updated, dict(applied=bool(changes), phrase_ids=changes,
                         hypotheses=[whisper,qwen], proposals=accepted)
