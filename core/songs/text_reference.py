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
    result=check_reference(url)
    if parsed.hostname in {'genius.com','www.genius.com','musixmatch.com','www.musixmatch.com'} and not result.get('has_lyrics_text'):
        from .browser_reference import fetch
        rendered=fetch(url)
        if rendered.get('status')=='available':return rendered
        result['error']=rendered.get('error') or 'Текст страницы недоступен; можно вставить его вручную'
        result['status']='unavailable'
    return result


def web_search(query, *, blocked=None):
    """Public search, bounded timeout. Snippets never count as lyric evidence."""
    from .browser_search import search as browser_search
    browser_errors=[]
    try:
        engines=[e for e in ('yandex','google') if e not in (blocked or {})]
        result=browser_search(query,engines=engines) if engines else {'hits':[],'engines':[]}
        if blocked is not None:
            result['engines']+=list(blocked.values())
            for e in result.get('engines',[]):
                if e.get('engine') and e.get('status')=='captcha':blocked[e['engine']]=e
        web_search.last_engines=result.get('engines',[])
        if result.get('hits'):return result['hits']
        browser_errors=[str(e.get('engine','browser'))+': '+str(e.get('message',e['status']))
                        for e in result.get('engines',[]) if e.get('status') in {'captcha','error','unavailable'}]
    except Exception as exc:
        browser_errors=[str(exc)[:150]]
        web_search.last_engines=[{'status':'error','message':browser_errors[0]}]
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
    errors=browser_errors
    for provider in ('ddg','bing'):
        try:
            url=('https://html.duckduckgo.com/html/?'+urlencode({'q':query,'kl':'ru-ru'}) if provider=='ddg'
                 else 'https://www.bing.com/search?'+urlencode({'q':query,'format':'rss','mkt':'ru-RU','setlang':'ru'}))
            with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=8) as response:
                body=response.read(1_000_001)
            if len(body)>1_000_000: raise ValueError('Слишком большая поисковая выдача')
            if provider=='ddg':
                decoded=body.decode('utf-8','replace')
                if any(marker in decoded.lower() for marker in ('anomaly.js','challenge-form','bots use duckduckgo')):
                    raise RuntimeError('DuckDuckGo: требуется проверка в браузере')
                parser=Links();parser.feed(decoded); rows=parser.links
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
    # ASR agreement is evidence, not a user edit. In particular an agreed
    # neighbouring word must not freeze a disputed word-boundary replacement.
    return phrase.get('review_state')=='verified' or bool(phrase.get('manual_edited'))


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
            source=next((r for r in references if r.get('url')==item['alternative'].get('url')), {})
            confirmation=source.get('text_confirmation') or {}
            item['source_confirmed']=bool(source.get('user_confirmed') or
                (source.get('text_confirmed') and confirmation.get('confirmed') and
                 len(confirmation.get('hits',[]))>=3))
            evidence=verify(phrase,item)
            item['audio_evidence']=evidence
            if evidence.get('confirmed'):
                item.update(status=('confirmed_by_source_and_audio' if evidence.get('basis')=='source_and_audio'
                                    else 'confirmed_by_audio'),auto_apply=True)
                accepted.setdefault(phrase['phrase_id'],[]).append(item)
            else:
                item['status']='needs_target_audio_check'
        except Exception as exc:
            item['audio_error']=str(exc)[:300]
    for phrase in result:
        edits=sorted(accepted.get(phrase['phrase_id'],[]),key=lambda p:p['current']['char_start'],reverse=True)
        edge=len(phrase['text'])
        for item in edits:
            a,b=item['current']['char_start'],item['current']['char_end']
            if b>edge or phrase['text'][a:b]!=item['current']['text']:
                item.update(status='overlapping_candidate',auto_apply=False);continue
            # Keep measured times for unaffected words while the align stage
            # refines the edited span. Never stretch lyrics over a lead-in.
            from .text_sources import edit_span
            edit_span(phrase,a,b,item['alternative']['text'])
            phrase['alignment_stale']=True
            edge=a
    return result,proposals


def run(phrases, context=None, *, search=None, fetch=None, verify=None, progress=lambda _:None):
    # A CAPTCHA blocks a provider for this run; do not repeatedly relaunch it
    # for every lyric query. Other providers and direct pages remain usable.
    default_search=search is None or search is web_search
    blocked={}
    if default_search:search=lambda query:web_search(query,blocked=blocked)
    fetch=fetch or fetch_source; context=context or {}
    selected_run=next((r for r in context.get('analysis_runs',[]) if r.get('run_id')==context.get('selected_run_id')), {})
    hypotheses=context.get('hypotheses') or selected_run.get('hypotheses')
    if hypotheses:phrases=refresh_agreement(phrases,hypotheses)
    selected=anchors(phrases,limit=12)
    report={'state':'searching','anchors':selected,'references':[],'chord_sources':[],
            'queries':[],'checked_pages':[],'errors':[],'candidates':[],'suggested_sources':[],
            'applied':0,'checked_at':time.time()}
    deadline=time.monotonic()+180;seen=set(); fingerprints=set(); checked=0
    def inspect(candidate, want_chords=False):
        nonlocal checked
        url=urldefrag(str(candidate.get('url') or ''))[0]
        key=url or candidate.get('candidate_id')
        pasted=candidate.get('manual_source') and candidate.get('plain_lyrics')
        if (not url.startswith(('https://','http://')) and not pasted) or key in seen or checked>=(20 if want_chords else 16) or time.monotonic()>deadline:return
        seen.add(key);checked+=1
        try:
            page=({'status':'available','extracted_text':candidate['plain_lyrics'],'page_title':candidate.get('title'),'final_url':url} if pasted else fetch(url))
            if page.get('status')!='available':
                report['checked_pages'].append({'url':url,'status':page.get('status'),'http_status':page.get('http_status')});return
            text=lyrics_only(page.get('extracted_text',''))
            match=confirm_text(text,selected)
            report['checked_pages'].append({'url':url,'status':match['status'],'exact_matches':len(match['hits'])})
            final=page.get('final_url') or url
            ref={'candidate_id':'text-reference:'+hashlib.sha256((final or text).encode()).hexdigest()[:16],
                 'kind':'lyrics','url':final,'title':page.get('page_title') or candidate.get('title') or 'Текст песни / стихотворение',
                 'plain_lyrics':text,'text_confirmed':match['confirmed'] or bool(candidate.get('user_confirmed')),'text_confirmation':match,
                 'version_verified':False,'source_check':{k:v for k,v in page.items() if k!='extracted_text'}}
            for field in ('user_confirmed','manual_source'):
                if candidate.get(field):ref[field]=True
            if not ref['text_confirmed']:
                if len(text)>=40 and (page.get('has_lyrics_text') or match['hits']):report['suggested_sources'].append(ref)
                return
            if page.get('chords_extractable'):
                chord={**ref,'kind':'chords','candidate_id':ref['candidate_id'].replace('text-reference:','chord-reference:')}
                if final not in {r['url'] for r in report['chord_sources']}:
                    report['chord_sources'].append(chord)
            fingerprint=hashlib.sha256(' '.join(t['norm'] for t in tokens(text)).encode()).hexdigest()
            if len(report['references'])<2 and fingerprint not in fingerprints:
                report['references'].append(ref);fingerprints.add(fingerprint)
        except Exception as exc:
            report['errors'].append({'url':url,'error':str(exc)[:300]})
    # The user's selected text source precedes cached catalogue references.
    for candidate in (context.get('text_reference') or {}).get('references',[])+context.get('source_candidates',[]):
        if checked>=4:break  # Reserve page slots for newly discovered publications.
        if candidate.get('kind') in {'lyrics','reference','chords'}: inspect(candidate)
    plan=query_plan(selected,context.get('title',''),context.get('artist',''),limit=8)
    # Search by recognized text for chord pages as well, not an assumed artist.
    plan += [{'query':'"'+a['text'].replace('"','')+'" аккорды','stage':'chords'} for a in selected[:2]]
    for query in plan:
        if time.monotonic()>deadline or checked>=20:
            report['errors'].append({'error':'Достигнут лимит времени или страниц; поиск выполнен не полностью'})
            break
        if query['stage']!='chords' and len(report['references'])>=2:continue
        progress('Сверка с источником текста: '+('ищем аккорды по фразам' if query['stage']=='chords' else 'ищем текст по согласованным фразам'))
        report['queries'].append({'query':query['query'],'stage':query['stage']})
        try:
            found=search(query['query'])
            if default_search:
                report.setdefault('search_engines',[]).append({'query':query['query'],'engines':web_search.last_engines})
            for candidate in found[:3]: inspect(candidate,query['stage']=='chords')
        except Exception as exc:
            report['errors'].append({'query':query['query'],'error':str(exc)[:300]})
            if default_search:
                report.setdefault('search_engines',[]).append({'query':query['query'],'engines':getattr(web_search,'last_engines',[])})
    report['chord_sources']=report['chord_sources'][:3]
    if not report['references']:
        report['state']=('needs_confirmation' if report['suggested_sources'] else 'search_error' if report['errors']
                         else 'insufficient_consensus' if len(selected)<3 else 'not_found')
        return copy.deepcopy(phrases),report
    progress('Сверка с источником текста: сравниваем расхождения с записью')
    updated,report['candidates']=reconcile(phrases,report['references'],verify)
    report['applied']=sum(bool(c.get('auto_apply')) for c in report['candidates'])
    report['state']='compared'
    report['comparisons']=[compare_reference(updated,r) for r in report['references']]
    return updated,report


def result_patch(row, report):
    """Merge this stage's sources without changing the selected audio recording."""
    refs=report.get('references',[])+report.get('chord_sources',[])+report.get('suggested_sources',[])
    candidates=[copy.deepcopy(r) for r in row.get('source_candidates') or []
                if not str(r.get('candidate_id','')).startswith(('text-reference:','chord-reference:'))]
    for ref in refs:
        candidates=[r for r in candidates if r.get('candidate_id')!=ref.get('candidate_id')]
        candidates.append(copy.deepcopy(ref))
    return {'text_reference':copy.deepcopy(report),'source_candidates':candidates}
