"""Lyric-first retrieval after ASR: three independent text anchors, then audio checks.

Publications are evidence, not a replacement transcript or a timing source.
The detached song worker owns all network/inference work. No app settings loaded.
"""
import copy
import hashlib
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit, parse_qs, urldefrag
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .reference_fragments import anchors, query_plan, publication_match, lyrics_only, refresh_agreement
from .reference_text import tokens, reference_candidates
from .sources import check_reference
from .evidence import compare_reference


def fetch_source(url):
    parsed=urlsplit(url)
    match=re.fullmatch(r'/api/get/(\d+)',parsed.path)
    if parsed.hostname=='lrclib.net' and match:
        # Refresh the actual API document, rather than confirming a search
        # snippet or an old cached body whose URL may no longer be available.
        canonical='https://lrclib.net/api/get/'+match.group(1)
        from .sources import _NoRedirect
        from urllib.request import build_opener
        request=Request(canonical,headers={'User-Agent':'Dewiar-Songs/0.1 (personal music study)'})
        with build_opener(_NoRedirect()).open(request,timeout=8) as response:
            body=response.read(2_000_001)
        if len(body)>2_000_000:raise ValueError('Слишком большой текст источника')
        data=json.loads(body)
        return {'status':'available','extracted_text':str(data.get('plainLyrics') or ''),
                'final_url':canonical,'page_title':str(data.get('trackName') or ''),
                'checked_at':time.time(),'http_status':200}
    return check_reference(url)


def web_search(query):
    """Public search, bounded timeout. Snippets never count as lyric evidence."""
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(); self.links=[]; self.link=None
        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if tag=='a' and 'result__a' in attrs.get('class',''):
                url=attrs.get('href','')
                if url.startswith('//'): url='https:'+url
                url=parse_qs(urlsplit(url).query).get('uddg',[url])[0]
                self.link={'url':url,'title':''}; self.links.append(self.link)
        def handle_data(self, data):
            if self.link is not None: self.link['title']+=data
        def handle_endtag(self, tag):
            if tag=='a': self.link=None
    errors=[]
    for provider in ('ddg','bing'):
        try:
            url=('https://html.duckduckgo.com/html/?'+urlencode({'q':query,'kl':'ru-ru'}) if provider=='ddg'
                 else 'https://www.bing.com/search?'+urlencode({'q':query,'format':'rss','mkt':'ru-RU','setlang':'ru'}))
            with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=8) as response:
                body=response.read(1_000_001)
            if len(body)>1_000_000: raise ValueError('Слишком большая поисковая выдача')
            if provider=='ddg':
                parser=Links();parser.feed(body.decode('utf-8','replace')); rows=parser.links
            else:
                rows=[{'url':item.findtext('link'),'title':item.findtext('title'),'snippet':item.findtext('description')}
                      for item in ElementTree.fromstring(body).findall('./channel/item')]
                # RSS sometimes ignores a quoted Russian query completely.
                # Do not spend the page budget on unrelated results. This is
                # only a retrieval filter; confirmation still reads the page.
                wanted={t['norm'] for t in tokens(query) if len(t['norm'])>=4}
                rows=[r for r in rows if len(wanted & {t['norm'] for t in tokens(
                    (r.get('title') or '')+' '+(r.get('snippet') or ''))})>=min(2,len(wanted))]
            rows=[r for r in rows if str(r.get('url','')).startswith(('https://','http://'))]
            if rows: return rows[:5]
        except Exception as exc:
            errors.append(str(exc)[:150])
    if errors: raise RuntimeError('; '.join(errors))
    return []


def confirm_text(text, selected):
    match=publication_match(text,selected)
    hits=[h for h in match['hits'] if h['exact_full_anchor']]
    return {**match,'hits':hits,'confirmed':len(hits)>=3,
            'status':'confirmed' if len(hits)>=3 else 'insufficient_matches'}


def _protected(phrase, start, end):
    if phrase.get('review_state')=='verified' or phrase.get('manual_edited'): return True
    return any(a.get('char_start',-1)<end and a.get('char_end',-1)>start
               and a.get('agree',0)>=2 and a.get('agree')==a.get('models')
               for a in phrase.get('agreement',[]))


def reconcile(phrases, references, verify=None):
    """Change only bounded, audio-confirmed differences; retain all repeats/times."""
    result=copy.deepcopy(phrases)
    proposals=reference_candidates(result,references)
    accepted={}; checked=0
    for item in proposals:
        current=item['current']; phrase=next((p for p in result if p.get('phrase_id')==current['phrase_id']),None)
        if not phrase or _protected(phrase,current['char_start'],current['char_end']):
            item['status']='protected_text';continue
        # Conflicting publications remain alternatives, never a majority vote.
        choices={p['alternative']['text'] for p in proposals if p['current']==current}
        if len(choices)>1:
            item['status']='sources_disagree';continue
        if verify is None or checked>=8:
            continue
        checked+=1
        try:
            evidence=verify(phrase,item)
            item['audio_evidence']=evidence
            if evidence.get('confirmed'):
                item.update(status='confirmed_by_audio',auto_apply=True)
                accepted.setdefault(phrase['phrase_id'],[]).append(item)
        except Exception as exc:
            item['audio_error']=str(exc)[:300]
    for phrase in result:
        edits=sorted(accepted.get(phrase['phrase_id'],[]),key=lambda p:p['current']['char_start'],reverse=True)
        edge=len(phrase['text'])
        for item in edits:
            a,b=item['current']['char_start'],item['current']['char_end']
            if b>edge or phrase['text'][a:b]!=item['current']['text']:
                item.update(status='overlapping_candidate',auto_apply=False);continue
            phrase.setdefault('text_before_reference',phrase['text'])
            phrase['text']=phrase['text'][:a]+item['alternative']['text']+phrase['text'][b:]
            phrase.update(words=[],agreement=[],alignment_stale=True,timing_quality='phrase',review_state='needs_review')
            edge=a
    return result,proposals


def run(phrases, context=None, *, search=None, fetch=None, verify=None, progress=lambda _:None):
    search=search or web_search; fetch=fetch or fetch_source; context=context or {}
    selected_run=next((r for r in context.get('analysis_runs',[]) if r.get('run_id')==context.get('selected_run_id')), {})
    hypotheses=context.get('hypotheses') or selected_run.get('hypotheses')
    if hypotheses:phrases=refresh_agreement(phrases,hypotheses)
    selected=anchors(phrases,limit=6)
    report={'state':'searching','anchors':selected,'references':[],'chord_sources':[],
            'queries':[],'checked_pages':[],'errors':[],'candidates':[],'applied':0,'checked_at':time.time()}
    if len(selected)<3:
        report['state']='insufficient_consensus';return copy.deepcopy(phrases),report
    deadline=time.monotonic()+100;seen=set(); fingerprints=set(); checked=0
    def inspect(candidate, want_chords=False):
        nonlocal checked
        url=urldefrag(str(candidate.get('url') or ''))[0]
        if not url.startswith(('https://','http://')) or url in seen or checked>=(12 if want_chords else 8) or time.monotonic()>deadline:return
        seen.add(url);checked+=1
        try:
            page=fetch(url)
            if page.get('status')!='available':
                report['checked_pages'].append({'url':url,'status':page.get('status'),'http_status':page.get('http_status')});return
            text=lyrics_only(page.get('extracted_text',''))
            match=confirm_text(text,selected)
            report['checked_pages'].append({'url':url,'status':match['status'],'exact_matches':len(match['hits'])})
            if not match['confirmed']:return
            final=page.get('final_url') or url
            ref={'candidate_id':'text-reference:'+hashlib.sha256(final.encode()).hexdigest()[:16],
                 'kind':'lyrics','url':final,'title':page.get('page_title') or candidate.get('title') or 'Текст песни / стихотворение',
                 'plain_lyrics':text,'text_confirmed':True,'text_confirmation':match,
                 'version_verified':False,'source_check':{k:v for k,v in page.items() if k!='extracted_text'}}
            if page.get('chords_extractable'):
                chord={**ref,'kind':'chords','candidate_id':ref['candidate_id'].replace('text-reference:','chord-reference:')}
                if final not in {r['url'] for r in report['chord_sources']}:
                    report['chord_sources'].append(chord)
            fingerprint=hashlib.sha256(' '.join(t['norm'] for t in tokens(text)).encode()).hexdigest()
            if len(report['references'])<2 and fingerprint not in fingerprints:
                report['references'].append(ref);fingerprints.add(fingerprint)
        except Exception as exc:
            report['errors'].append({'url':url,'error':str(exc)[:300]})
    for candidate in context.get('source_candidates',[]):
        if candidate.get('kind') in {'lyrics','reference','chords'}: inspect(candidate)
    plan=query_plan(selected[:3],context.get('title',''),context.get('artist',''),limit=8)
    # Search by recognized text for chord pages as well, not an assumed artist.
    plan += [{'query':'"'+a['text'].replace('"','')+'" аккорды','stage':'chords'} for a in selected[:2]]
    for query in plan:
        if time.monotonic()>deadline or checked>=12:break
        if query['stage']!='chords' and len(report['references'])>=2:continue
        progress('Сверка с источником текста: '+('ищем аккорды по фразам' if query['stage']=='chords' else 'ищем текст по согласованным фразам'))
        report['queries'].append({'query':query['query'],'stage':query['stage']})
        try:
            for candidate in search(query['query']): inspect(candidate,query['stage']=='chords')
        except Exception as exc:
            report['errors'].append({'query':query['query'],'error':str(exc)[:300]})
    report['chord_sources']=report['chord_sources'][:3]
    if not report['references']:
        report['state']='search_error' if report['errors'] else 'not_found'
        return copy.deepcopy(phrases),report
    progress('Сверка с источником текста: сравниваем расхождения с записью')
    updated,report['candidates']=reconcile(phrases,report['references'],verify)
    report['applied']=sum(bool(c.get('auto_apply')) for c in report['candidates'])
    report['state']='compared'
    report['comparisons']=[compare_reference(updated,r) for r in report['references']]
    return updated,report


def result_patch(row, report):
    """Merge this stage's sources without changing the selected audio recording."""
    refs=report.get('references',[])+report.get('chord_sources',[])
    candidates=[copy.deepcopy(r) for r in row.get('source_candidates') or []
                if not str(r.get('candidate_id','')).startswith(('text-reference:','chord-reference:'))]
    for ref in refs:
        candidates=[r for r in candidates if r.get('candidate_id')!=ref.get('candidate_id')]
        candidates.append(copy.deepcopy(ref))
    return {'text_reference':copy.deepcopy(report),'source_candidates':candidates}
