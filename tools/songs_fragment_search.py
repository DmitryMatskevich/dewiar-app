"""Offline construction of lyric-first search queries for a read-only trial.

No network calls or state mutations. Search evidence is not proof of ASR accuracy.
"""
import argparse
import json
from pathlib import Path
import re

from songs_text_postpass import tokens, vocalization

STOP = set('и а но не ни в во на к с со по за из у от до об обо о для при я ты он она мы вы они это то как что когда где же бы ли да нет мне тебе нас вас его ее свой мои моя'.split())


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
    """First attempts use text only. Metadata is an optional last fallback."""
    out=[]
    def add(query, stage, anchor):
        query=re.sub(r'\s+', ' ', query).strip()
        if query and query not in {q['query'] for q in out} and len(out) < limit:
            out.append(dict(query=query, stage=stage, anchor=anchor))
    for a in selected:
        # Quotes in lyrics are punctuation, not query operators.
        q=' '.join(t['text'] for t in tokens(a['text']))
        add('"'+q+'"','exact_fragment',a)
    for a in selected:
        short=' '.join(t['text'] for t in tokens(a['text'])[:5])
        add('"'+short+'"','short_fragment',a)
    if selected:
        plain=' '.join(t['text'] for t in tokens(selected[0]['text']))
        add(plain+' стих текст','poem_or_song',selected[0])
        if title or artist:
            add(plain+' '+title+' '+artist,'metadata_fallback',selected[0])
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


def lyrics_only(text):
    """Remove chord-only lines from read HTML; never strip words inline."""
    chord=re.compile(r'^[A-G](?:#|b)?(?:m7b5|m(?:aj)?\d*|maj\d*|dim\d*|aug\d*|sus\d*|add\d*|\d+)?(?:/[A-G](?:#|b)?)?$')
    lines=[]
    for line in text.splitlines():
        parts=line.strip().strip('|').split()
        if parts and all(chord.fullmatch(p.strip('|,')) for p in parts):
            continue
        if re.fullmatch(r'\s*\[?(?:Припев|Куплет|Вступление|Проигрыш|Бридж)\s*\d*\]?:?\s*',line,re.I):
            continue
        lines.append(line)
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--refined',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    root=args.library.resolve();out=args.output.resolve()
    if root==out or root in out.parents:p.error('Output must be separate from the library')
    rows=[]
    for file in sorted(root.glob('*/song.json')):
        song=json.loads(file.read_text())
        pool=song.get('phrases',[])
        selected=anchors(pool)
        if len(selected)<3 and args.refined:
            path=args.refined/file.parent.name/'result.json'
            if path.is_file():
                extra=json.loads(path.read_text()).get('selected',{}).get('phrases',[])
                # Preserve origins for auditing which analysis supplied a query.
                pool=pool+[dict(p,phrase_id='trial:'+str(p.get('phrase_id'))) for p in extra]
                selected=anchors(pool)
        rows.append(dict(song_id=file.parent.name,title=song.get('title'),
            anchors=selected,queries=query_plan(selected,song.get('title',''),song.get('artist',''))))
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    for r in rows:
        print(r['song_id'],r['title'])
        for q in r['queries'][:3]:print(' ',q['query'])
        if not r['anchors']:print(' NO RELIABLE FRAGMENT')


if __name__=='__main__':main()
