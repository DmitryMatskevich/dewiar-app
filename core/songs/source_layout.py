"""Verse lines, stanzas and labels of the sung text from its confirmed publication.

A publication prints the poem: its lines, quatrains separated by a blank
line and labels («Припев:»). The recognizers know none of it, and stanzas
guessed from pauses («four lines, split at the longest pause») matched the
publication in a quarter of the cases (34 songs, 14.11).

The sung text is aligned with the publication word by word, never line by
line: the two are cut differently («Кто поверит? Я и сам не верю» is one
printed line and two sung phrases). A refrain printed once and sung four
times is matched on every repeat. Only words in agreeing runs take the
publication's structure; the rest of the song keeps the layout by pauses.
"""
import difflib

from .reference_text import tokens

# Below this share of sung words found in the publication, it is another
# version of the song (or another song): its structure is not used.
MIN_COVERAGE = 0.5
LEAD_WORDS = {'я', 'а', 'и', 'но', 'ну', 'да', 'вот', 'ведь', 'ой', 'эх', 'ах', 'так', 'же', 'он', 'мы', 'ты'}
INSTRUMENTAL = ('вступление', 'проигрыш', 'соло', 'кода', 'концовка', 'окончание', 'интро', 'аутро',
                'intro', 'outro', 'solo')


def confirmed_references(row):
    report = row.get('text_reference') or {}
    return [ref for ref in report.get('references') or []
            if ref.get('plain_lyrics') and (ref.get('user_confirmed') or ref.get('text_confirmed'))]


def publication(ref):
    """Words of a publication with their line, stanza and label."""
    labels = {}
    for label in ref.get('labels') or []:
        if isinstance(label, dict) and isinstance(label.get('line'), int) and label.get('text'):
            labels.setdefault(label['line'], label['text'])
    out, line_no, stanza, new = [], -1, -1, True
    for k, line in enumerate(str(ref.get('plain_lyrics') or '').splitlines()):
        words = tokens(line)
        if not words:
            new = new or not line.strip()
            continue
        # A labelled part («Припев:», «Куплет 2:», «Проигрыш») starts a stanza
        # even where the page has no blank line before it.
        label = labels.get(k)
        new = new or bool(label)
        if label and label.casefold().startswith(INSTRUMENTAL):
            label = None        # the sheet labels its breaks itself
        line_no += 1
        if new:
            stanza += 1
        for j, word in enumerate(words):
            out.append(dict(norm=word['norm'], line=line_no, stanza=stanza, line_start=j == 0, pos=j,
                            stanza_start=new and j == 0, first_line=new, label=label))
        new = False
    return out


def sung_words(rows):
    """Words of the sung rows, keyed by phrase and character in the phrase."""
    out = []
    for row in rows:
        text = str(row.get('text') or '')
        if not text.strip() or text.strip() == '???' or row.get('vocal_evidence') == 'none':
            continue
        offset = int(row.get('source_char_offset') or 0)
        for word in tokens(text):
            out.append(dict(norm=word['norm'], key=(row.get('phrase_id'), offset + word['start'])))
    return out


def _runs(indexes):
    run = []
    for i in indexes:
        if run and i != run[-1] + 1:
            yield run
            run = []
        run.append(i)
    if run:
        yield run


def _match_units(sung, pub, units, mapping):
    """Map words not yet mapped to repeats of publication units (stanzas, lines)."""
    sung_norms = [w['norm'] for w in sung]
    for unit in units:
        pn = [pub[i]['norm'] for i in unit]
        n = len(pn)
        if n < 3:
            continue
        free = [i for i in range(len(sung)) if i not in mapping]
        for run in _runs(free):
            if len(run) < max(3, int(n * .6)):
                continue
            start, end = run[0], run[-1] + 1
            while start < end:
                # A repeat begins with one of the unit's first two words.
                if sung_norms[start] not in pn[:2]:
                    start += 1
                    continue
                best = None
                for size in range(max(3, int(n * .7)), min(int(n * 1.3) + 1, end - start) + 1):
                    sm = difflib.SequenceMatcher(None, sung_norms[start:start + size], pn, autojunk=False)
                    equal = sum(b.size for b in sm.get_matching_blocks())
                    score = equal / max(size, n)
                    if equal >= max(3, .6 * n) and (best is None or score > best[0]):
                        best = (score, size, sm)
                if best is None or best[0] < .6:
                    start += 1
                    continue
                for a, b, size in best[2].get_matching_blocks():
                    if size >= 2 or (size == 1 and n <= 6):
                        for d in range(size):
                            mapping[start + a + d] = unit[b + d]
                start += best[1]


def align(sung, pub):
    """Sung word index → publication word index, for agreeing runs only."""
    mapping = {}
    sm = difflib.SequenceMatcher(None, [w['norm'] for w in sung], [w['norm'] for w in pub], autojunk=False)
    for a, b, size in sm.get_matching_blocks():
        if size >= 2:
            for d in range(size):
                mapping[a + d] = b + d
    stanzas, lines = {}, {}
    for i, word in enumerate(pub):
        stanzas.setdefault(word['stanza'], []).append(i)
        lines.setdefault(word['line'], []).append(i)
    _match_units(sung, pub, list(stanzas.values()), mapping)
    _match_units(sung, pub, list(lines.values()), mapping)
    return mapping


def word_layout(rows, references):
    """``{(phrase_id, char): flags}`` for sung words in agreeing runs, and the coverage.

    Flags: ``line`` — a printed line starts at this word; ``stanza`` — a
    printed stanza starts here; ``joined`` — the word continues the printed
    line of the previous word (no line end between them); ``label``.
    """
    sung = sung_words(rows)
    if not sung:
        return {}, 0.0
    found = []
    for ref in references or []:
        pub = publication(ref)
        if pub:
            found.append((pub, align(sung, pub)))
    if not found:
        return {}, 0.0
    # Of publications that agree about as well, the one printing its parts
    # (stanzas, «Припев:») tells more.
    most = max(len(mapping) for _, mapping in found)
    pub, mapping = max((item for item in found if len(item[1]) >= .95 * most),
                       key=lambda item: (len({w['stanza'] for w in item[0]}) > 1,
                                         sum(1 for w in item[0] if w['label']), len(item[1])))
    coverage = len(mapping) / len(sung)
    if coverage < MIN_COVERAGE:
        return {}, coverage
    flags = {}
    for i, j in mapping.items():
        word = pub[j]
        previous, following = mapping.get(i - 1), mapping.get(i + 1)
        line_words = sum(1 for w in pub if w['line'] == word['line']) if word['line_start'] else 0
        # A printed line counts once its next word is sung right after: one
        # word at the edge of a run («…и уйду | я» + «просто») proves nothing.
        confirmed = word['line_start'] and (line_words == 1 or following == j + 1)
        flags[i] = dict(line=confirmed, stanza=confirmed and word['stanza_start'], label=word['label'],
                        first_line=word['first_line'], pos=word['pos'],
                        joined=previous is not None and previous == j - 1 and pub[previous]['line'] == word['line'])
    for i in sorted(flags):
        f = flags[i]
        if not f['line'] or i - 1 in mapping or i == 0:
            continue
        # «Я счастливый» sung for the printed «Счастливый»: a short word sung
        # before the printed line begins it.
        if sung[i - 1]['norm'] in LEAD_WORDS and (i - 2 not in mapping or pub[mapping[i - 2]]['line'] != pub[mapping[i]]['line']):
            flags[i - 1] = dict(f, pos=0, joined=False, loose=True)
            flags[i] = dict(f, line=False, stanza=False, joined=True, pos=f['pos'] + 1)
    # A printed line whose first word was misheard («Даль» for «То ль»):
    # the unmatched sung word right after a matched line end starts the line.
    for i in range(1, len(sung)):
        if i in mapping or i in flags or i - 1 not in mapping:
            continue
        j = mapping[i - 1]
        if j + 1 < len(pub) and pub[j + 1]['line_start'] and pub[j + 1]['line'] != pub[j]['line']:
            nxt = next((mapping[k] for k in range(i + 1, min(len(sung), i + 4)) if k in mapping), None)
            if nxt is not None and pub[nxt]['line'] == pub[j + 1]['line']:
                flags[i] = dict(line=True, stanza=pub[j + 1]['stanza_start'], label=pub[j + 1]['label'],
                                first_line=pub[j + 1]['first_line'], pos=0, joined=False, loose=True)
    # A publication printed without blank lines says nothing about stanzas:
    # its lines still guide the verse, blank lines come from breaks.
    stanzas = len({w['stanza'] for w in pub}) > 1
    return {sung[i]['key']: dict(f, stanzas=stanzas) for i, f in flags.items()}, coverage
