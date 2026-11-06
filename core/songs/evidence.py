"""Read-only checks of actual recognition and published reference texts.

Differences from a publication are not ASR errors: performances, repeats and
editions differ. Never rewrite lyrics or borrow word times from a reference.
"""
import difflib
import re


def _tokens(text):
    return re.findall(r"[\w]+(?:[-’'][\w]+)*", str(text).casefold().replace('ё', 'е'))


def phrase_warnings(phrase):
    text = phrase.get('text', '')
    words = _tokens(text)
    warnings = []
    if any(len(w) >= 80 and re.search(r'(.{1,12})\1{7,}', w) for w in words):
        warnings.append({'code': 'repetition_loop', 'label': 'Похоже на зацикливание распознавателя. Проверьте фрагмент; исходный текст сохранён.'})
    duration = (phrase.get('end_ms', 0)-phrase.get('start_ms', 0))/1000
    if duration > 0 and sum(map(len, words)) > max(40, 35*duration):
        warnings.append({'code': 'text_rate', 'label': 'Слишком много букв для указанной длительности. Текст или время могут быть ошибочными.'})
    if len(words) <= 3 and any(w in {'редактор', 'субтитры', 'корректор'} for w in words):
        warnings.append({'code': 'possible_credit', 'label': 'Возможная вставка распознавателя о титрах. Подтвердите по записи.'})
    return warnings


def compare_reference(phrases, reference):
    """Sequence differences, including missing/extra repeats, with audio anchors."""
    current, owners = [], []
    for p in phrases:
        words = _tokens(p.get('text', ''))
        current.extend(words)
        owners.extend([p]*len(words))
    published = _tokens(reference.get('plain_lyrics', ''))
    if not current or not published:
        return None
    # Bound work for untrusted/accidentally huge source documents.
    if len(current) > 6000 or len(published) > 6000:
        return {'status': 'too_large', 'differences': [], 'label': 'Текст слишком большой для построчной сверки'}
    differences = []
    equal = 0
    for tag, a, b, c, d in difflib.SequenceMatcher(None, current, published, autojunk=False).get_opcodes():
        if tag == 'equal':
            equal += b-a
            continue
        owner = owners[min(a, len(owners)-1)]
        differences.append({'kind': tag, 'recognized': ' '.join(current[a:b]),
            'published': ' '.join(published[c:d]), 'phrase_id': owner.get('phrase_id'),
            'start_ms': owner.get('start_ms'), 'anchor_estimated': tag == 'insert',
            'recognized_tokens': b-a, 'published_tokens': d-c})
    return {'status': 'compared', 'equal_tokens': equal, 'recognized_tokens': len(current),
            'published_tokens': len(published), 'differences': differences,
            'reference_id': reference.get('candidate_id'), 'url': reference.get('url'),
            'label': 'Различия с публикацией, не оценка точности распознавания',
            'version_verified': bool(reference.get('version_verified'))}


def review_evidence(row):
    phrases = row.get('phrases', [])
    issues = [{'phrase_id': p.get('phrase_id'), 'start_ms': p.get('start_ms'), **w}
              for p in phrases for w in phrase_warnings(p)]
    run = next((r for r in reversed(row.get('analysis_runs',[]))
                if r.get('run_id') == row.get('selected_run_id')), {})
    issues.extend(omission_warnings(phrases, run.get('hypotheses',[])))
    for entry in run.get('comparison',{}).get('quarantined_phrases',[]):
        p=entry['phrase']
        owner=min(phrases,key=lambda q:abs(q.get('start_ms',0)-p['start_ms']),default={})
        issues.append({'code':'possible_asr_credit','phrase_id':owner.get('phrase_id'),
            'start_ms':p['start_ms'],'end_ms':p['end_ms'],
            'label':f"{entry['label']} ({p['start_ms']/1000:.1f}–{p['end_ms']/1000:.1f} с): «{p['text']}»."})
    unresolved = [p for p in run.get('comparison',{}).get('proposals',[])
                  if p.get('status') != 'two_model_audio_agreement']
    for p in unresolved[:12]:
        start,end=p['start_ms'],p['end_ms']
        owner=next((q for q in phrases if q.get('start_ms',0)<end and q.get('end_ms',0)>start),{})
        issues.append({'code':'asr_disagreement','phrase_id':owner.get('phrase_id'),
            'start_ms':start,'end_ms':end,
            'label':f'Распознаватели расходятся около {start/1000:.1f}–{end/1000:.1f} с; проверьте запись.'})
    references = [compare_reference(phrases, r) for r in row.get('source_candidates', [])
                  if r.get('kind') == 'lyrics' and r.get('plain_lyrics')]
    return {'issues': issues, 'references': [r for r in references if r]}


def omission_warnings(phrases, hypotheses):
    """Expose unresolved coverage disagreements instead of calling them ready."""
    warnings = []
    seen = set()
    for hypothesis in hypotheses:
        for source in hypothesis.get('phrases',[]):
            words = _tokens(source.get('text',''))
            if len(words) < 6:
                continue
            start,end = source.get('start_ms',0),source.get('end_ms',0)
            local = [p for p in phrases if p.get('start_ms',0)<end and p.get('end_ms',0)>start]
            current = _tokens(' '.join(p.get('text','') for p in local))
            matched = sum(m.size for m in difflib.SequenceMatcher(None,words,current,autojunk=False).get_matching_blocks())
            key=(start,end)
            if matched > len(words)*.35 or key in seen:
                continue
            seen.add(key)
            owner = next(iter(local),min(phrases,key=lambda p:abs(p.get('start_ms',0)-start),default={}))
            warnings.append({'code':'possible_omission','phrase_id':owner.get('phrase_id'),
                'start_ms':start,'end_ms':end,'model':hypothesis.get('model'),
                'label':f'Возможен пропуск текста около {start/1000:.1f}–{end/1000:.1f} с: другой распознаватель содержит больше слов. Проверьте запись.'})
    return warnings
