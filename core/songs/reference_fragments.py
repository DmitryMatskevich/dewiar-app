"""Offline construction of lyric-first search queries for a read-only trial.

No network calls or state mutations. Search evidence is not proof of ASR accuracy.
"""
import argparse
import json
from pathlib import Path
import re

from .reference_text import tokens, vocalization

STOP = set('и а но не ни в во на к с со по за из у от до об обо о для при я ты он она мы вы они это то как что когда где же бы ли да нет мне тебе нас вас его ее свой мои моя'.split())


def refresh_agreement(phrases, hypotheses):
    """Rebuild legacy votes from real independent ASR outputs, not a dummy baseline.

    The displayed/manual text is a target, not an extra independent model vote.
    Recomputing also rejects stale offsets after edits without changing the text.
    """
    from .consensus import consensus
    from .reconciliation import family
    available={}
    for hyp in hypotheses:
        if hyp.get('model') and hyp.get('phrases'):
            available[family(hyp['model'])]=hyp
    updated=consensus(phrases,list(available.values()),choose_majority=False)
    for old,new in zip(phrases,updated):
        new['review_state']=old.get('review_state','needs_review')
        for vote in new.get('agreement',[]):
            vote['agree']=max(0,vote['agree']-1)
            vote['models']=max(0,vote['models']-1)
            vote['needs_review']=vote['agree']<2 or vote['agree']!=vote['models']
    return updated


def anchors(phrases, limit=3):
    if limit <= 0:
        return []
    candidates = []
    for p in phrases:
        text = p.get('text', '')
        ts = tokens(text)
        approved = p.get('review_state') == 'verified'
        votes = p.get('agreement', [])
        def trusted(t):
            if approved:
                return True
            for a in votes:
                start, end = a.get('char_start', -1), a.get('char_end', -1)
                # Refuse stale vote offsets after a manual text edit.
                if (start <= t['start'] and end >= t['end'] and a.get('agree', 0) >= 2
                        and a.get('agree', 0) == a.get('models', a.get('agree', 0))
                        and all([x['norm'] for x in tokens(value)] == [x['norm'] for x in tokens(a.get('text', ''))]
                                for value in a.get('alternatives', []))
                        and not a.get('needs_review') and start >= 0 and end <= len(text)
                        and [x['norm'] for x in tokens(text[start:end])] ==
                            [x['norm'] for x in tokens(a.get('text', ''))]):
                    return True
            return False
        groups, group = [], []
        for t in ts:
            if group and re.search(r'[.!?\n]', text[group[-1]['end']:t['start']]):
                groups.append(group); group=[]
            if trusted(t) and not vocalization(t['norm']):
                group.append(t)
            else:
                if group: groups.append(group); group=[]
        if group: groups.append(group)
        for g in groups:
            if len(g) < 4:
                continue
            # Keep windows contiguous; never join across an uncertain word.
            for i in range(len(g)-3):
                part = g[i:i+8]
                while part and part[-1]['norm'] in STOP:
                    part = part[:-1]
                content = {t['norm'] for t in part if len(t['norm']) >= 4 and t['norm'] not in STOP}
                if len(part) < 4 or len(content) < 2 or len({t['norm'] for t in part}) < 4:
                    continue
                phrase = text[part[0]['start']:part[-1]['end']]
                candidates.append(dict(text=phrase, phrase_id=p.get('phrase_id'),
                    start_ms=p.get('start_ms'), end_ms=p.get('end_ms'),
                    char_start=part[0]['start'], char_end=part[-1]['end'],
                    evidence='human_verified' if approved else 'model_agreement',
                    score=(100 if approved else 0)+min(len(part),8)+sum(min(len(w),9) for w in content)/10,
                    words=[t['norm'] for t in part]))
    result=[]
    for c in sorted(candidates, key=lambda x: -x['score']):
        # Different fragments/locations, not three overlapping windows or choruses.
        if any((c['phrase_id'] == q['phrase_id'] and
                c['char_start'] < q['char_end'] and c['char_end'] > q['char_start']) or
               len(set(c['words']) & set(q['words'])) / max(1,len(set(c['words']) | set(q['words']))) > .55
               for q in result):
            continue
        result.append(c)
        if len(result) >= limit:
            break
    return result


def query_plan(selected, title='', artist='', limit=8):
    """Diversify retrieval before retrying exact matches across verse boundaries.

    Discovery needs one phrase, confirmation still needs three independent
    trusted matches. Short complete fragments survive ASR punctuation and
    publication line breaks better than long concatenated verses.
    """
    out=[]
    def add(query, stage, anchor):
        query=re.sub(r'\s+', ' ', query).strip()
        if query and query not in {q['query'] for q in out} and len(out) < limit:
            out.append(dict(query=query, stage=stage, anchor=anchor))
    def cost(a):
        text=a['text']; count=len(tokens(text))
        # Prefer a coherent short phrase, not a suffix of one line plus the
        # start of the next. Confidence is already checked by anchors().
        return (len(re.findall(r'[,;:—–\n]',text)), abs(count-6), -a.get('score',0))
    chosen=sorted(selected,key=cost)[:3]
    for a in chosen:
        plain=' '.join(t['text'] for t in tokens(a['text']))
        add('"'+plain+'"','exact_fragment',a)
        add(plain+' стихи','poem_or_song',a)
    if chosen:
        a=chosen[0]
        plain=' '.join(t['text'] for t in tokens(a['text']))
        add(plain+' site:stihi.ru','poetry_site',a)
        if title or artist:
            add(plain+' '+title+' '+artist,'metadata_fallback',a)
        else:
            add(plain+' текст песни','song_text',a)
    elif title or artist:
        add(title+' '+artist+' текст','no_reliable_fragment',None)
    return out


def publication_match(text, selected):
    """Evidence from two non-overlapping anchors, not a same-version verdict."""
    ts=tokens(lyrics_only(text));words=[t['norm'] for t in ts]
    hits=[]
    for a in selected:
        needle=[t['norm'] for t in tokens(a['text'])]
        if any(needle==[t['norm'] for t in tokens(h['anchor'])] for h in hits):
            continue
        windows=[needle]+[needle[k:k+5] for k in range(len(needle)-4)]
        found=False
        for part in windows:
            if len(part)<4 or sum(w not in STOP and len(w)>=4 for w in part)<2:
                continue
            for i in range(len(words)-len(part)+1):
                if words[i:i+len(part)]==part:
                    end=i+len(part)
                    if not any(i<h['token_end'] and end>h['token_start'] for h in hits):
                        hits.append(dict(anchor=a['text'],matched=' '.join(part),
                                         exact_full_anchor=part==needle,
                                         phrase_id=a.get('phrase_id'),token_start=i,token_end=end))
                        found=True
                        break
            if found:break
    return dict(status='multiple_fragments' if len(hits)>=2 else
                'single_fragment' if hits else 'no_exact_fragment',
                hits=hits, version_verified=False)


CHORD_TOKEN = re.compile(r'^[A-H](?:#|b|♯|♭)?(?:maj|min|m|dim|aug|sus|add|M|\+|°|ø|\d+|b\d+|#\d+|\(\w+\))*'
                         r'(?:/[A-H](?:#|b|♯|♭)?)?$')
SECTION_LABEL = re.compile(r'^\s*\[?\s*(?:\d+\s*)?(?:Припев|Куплет|Вступление|Проигрыш|Бридж|Кода|Концовка|Окончание|Интро|Аутро|'
                           r'Соло|Chorus|Verse|Intro|Outro|Bridge|Solo|Pre-?chorus)\b[\s\d.]*(?:\(\s*\d+\s*р(?:аза?)?\s*\))?'
                           r'\s*[:\]]?\s*', re.I)
PLAYING_NOTE = re.compile(r'^\s*(?:Аккорды|Бой|Перебор|Ритм|Тональность|Каподастр|Capo|Тюнинг|Строй|Исполнитель|'
                          r'Слова|Музыка|Автор|Темп|Табулатура)\b[^:\n]{0,30}:|можно заменить|заменить на', re.I)


def _chord_line(line):
    # Repeat marks and separators of a chord line: «F Gadd9 Fm C - 2 раза», «| Am | G |».
    rest = re.sub(r'\b\d+\s*(?:р\.?|раза?|x|х)(?!\w)|(?<!\w)[xх]\s*\d+\b', ' ', line, flags=re.I)
    tokens = re.sub(r'[|,;()\[\]{}–—\-:]', ' ', rest).split()
    return bool(tokens) and all(CHORD_TOKEN.fullmatch(t) for t in tokens)


_UNITS = 'ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать ' \
         'четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать'.split()
_TENS = 'двадцать тридцать сорок пятьдесят шестьдесят семьдесят восемьдесят девяносто'.split()
_HUNDREDS = 'сто двести триста четыреста пятьсот шестьсот семьсот восемьсот девятьсот'.split()


def number_words(n):
    """0…1000 in words («100 балерин» is sung «сто балерин»)."""
    if n == 1000:
        return 'тысяча'
    words = []
    if n >= 100:
        words.append(_HUNDREDS[n // 100 - 1]); n %= 100
        if not n:
            return ' '.join(words)
    if n >= 20:
        words.append(_TENS[n // 10 - 2]); n %= 10
        if n:
            words.append(_UNITS[n])
    else:
        words.append(_UNITS[n])
    return ' '.join(words)


def lyrics_only(text):
    """Sung words from a page or a pasted text with chords.

    Removes chord lines (also with repeat marks), ChordPro marks ``[Am]``,
    section labels («Куплет 1:», «Припев:» — the sung rest of such a line is
    kept) and playing notes («Аккорды: …», «Бой: …», «Gadd9 можно заменить
    на G»). Words inside a lyric line are never removed.
    """
    lines = []
    for raw in str(text or '').splitlines():
        line = re.sub(r'\[[A-H][^\]\s]{0,12}\]', '', raw)
        if PLAYING_NOTE.search(line) or _chord_line(line):
            continue
        label = SECTION_LABEL.match(line)
        if label:
            line = line[label.end():]
            if not line.strip() or _chord_line(line):
                continue
        line = line if (label or line != raw) else raw
        # Publications write numbers as digits; the singer sings words.
        line = re.sub(r'(?<![\w.,:])(\d{1,4})(?![\w.,:]\d)(?!\w)',
                      lambda m: number_words(int(m.group(1))) if int(m.group(1)) <= 1000 else m.group(1), line)
        lines.append(line)
    return '\n'.join(lines)
