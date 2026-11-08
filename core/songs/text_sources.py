"""Explicit text-source selection and review. Audio selection/settings are untouched."""
import copy
import difflib
import hashlib
import re
import time

from .reference_fragments import anchors, lyrics_only, refresh_agreement
from .reference_text import tokens, reference_candidates
from .text_reference import fetch_source, confirm_text, result_patch


def checked_row(store, args):
    row=store.get(str(args.get('song_id') or ''))
    if not row:raise ValueError('Песня не найдена')
    if args.get('base_revision')!=row['revision']:raise ValueError('Песня уже изменилась — обновите карточку')
    if row.get('status') in {'running','recording'}:raise ValueError('Дождитесь завершения текущего этапа')
    return row


def compare(row, ref, report):
    report['suggested_sources']=[r for r in report.get('suggested_sources',[]) if r.get('candidate_id')!=ref['candidate_id']]
    if report.get('errors'):report['previous_search_errors']=report.pop('errors')
    report.update(state='compared',references=[ref],candidates=reference_candidates(row.get('phrases',[]),[ref]),
                  applied=0,user_applied=False,checked_at=time.time())
    # Source comparison is always an explicit, reversible suggestion. Manual
    # edits are not locked: the user may choose a source correction at any
    # time, and the phrase editor remains the place for a final wording change.
    return report


def edit_span(phrase, start, end, replacement):
    """Preserve untouched word times and silence; changed tokens get an explicit estimate."""
    old=phrase['text']; delta=len(replacement)-(end-start)
    touched=[]; words=[]
    for word in copy.deepcopy(phrase.get('words',[])):
        a,b=word.get('char_start',-1),word.get('char_end',-1)
        if a<0 or b<0:continue
        if b<=start:words.append(word)
        elif a>=end:
            word.update(char_start=a+delta,char_end=b+delta);words.append(word)
        else:touched.append(word)
    if touched:
        ts=tokens(replacement)
        # Align letters only to retain gaps between the original word intervals.
        # E.g. one misspelt word -> two words must not absorb a pause before
        # the following refrain. This maps time bounds, not acoustic evidence.
        old_letters='';owners=[]
        for i,w in enumerate(touched):
            letters=''.join(t['norm'] for t in tokens(w['text']))
            old_letters+=letters;owners.extend([i]*len(letters))
        new_letters=''.join(t['norm'] for t in ts);mapped={}
        for a,b,size in difflib.SequenceMatcher(None,old_letters,new_letters,autojunk=False).get_matching_blocks():
            for k in range(size):mapped[b+k]=owners[a+k]
        groups=[];offset=0
        for t in ts:
            ids=[mapped[k] for k in range(offset,offset+len(t['norm'])) if k in mapped]
            if not ids:ids=[min(len(touched)-1,round(offset/max(1,len(new_letters))*len(touched)))]
            groups.append((min(ids),max(ids)));offset+=len(t['norm'])
        used={}
        for i,t in enumerate(ts):
            group=groups[i];begin=touched[group[0]]['start_ms'];finish=touched[group[1]]['end_ms']
            weight=sum(len(ts[j]['text']) for j,g in enumerate(groups) if g==group)
            offset=used.get(group,0);next_offset=offset+len(t['text']);used[group]=next_offset
            words.append(dict(text=t['text'],char_start=start+t['start'],char_end=start+t['end'],
                              start_ms=round(begin+(finish-begin)*offset/weight),
                              end_ms=round(begin+(finish-begin)*next_offset/weight),method='reference_edit_estimated'))
    phrase.setdefault('text_before_reference',old)
    text=old[:start]+replacement+old[end:]
    for word in words:
        if word.get('method')=='reference_edit_estimated':
            punctuation=re.match(r'[.,!?;:…»”\"\)]+',text[word['char_end']:])
            if punctuation:
                word['char_end']+=len(punctuation.group());word['text']=text[word['char_start']:word['char_end']]
    phrase.update(text=text,words=sorted(words,key=lambda w:w['char_start']),
                  agreement=[],review_state='needs_review',reference_timing_review=True)


def command(store, action, args, *, fetch=fetch_source):
    row=checked_row(store,args); report=copy.deepcopy(row.get('text_reference') or {})
    if action=='add':
        url=str(args.get('url') or '').strip(); pasted=str(args.get('lyrics') or '').strip()
        if len(url)>2048 or len(pasted)>50000:raise ValueError('Источник слишком большой')
        if not url and not pasted:raise ValueError('Укажите ссылку на текст или вставьте текст песни')
        if url:
            from .sources import _public_http_url
            _public_http_url(url)
        try:
            page={'status':'available','extracted_text':pasted,'final_url':url,'page_title':row.get('title','Песня')+' — текст песни'} if pasted else fetch(url)
        except Exception as exc:
            page={'status':'unavailable','error':str(exc)[:300]}
        text=lyrics_only(page.get('extracted_text','')) if page.get('status')=='available' else ''
        selected=next((r for r in row.get('analysis_runs',[]) if r.get('run_id')==row.get('selected_run_id')), {})
        phrases=refresh_agreement(row.get('phrases',[]),selected['hypotheses']) if selected.get('hypotheses') else row.get('phrases',[])
        match=confirm_text(text,anchors(phrases,limit=12))
        ref={'candidate_id':'text-reference:'+hashlib.sha256((url or text).encode()).hexdigest()[:16],
             'kind':'lyrics','url':page.get('final_url') or url,'title':page.get('page_title') or url or 'Текст песни',
             'plain_lyrics':text,'text_confirmed':match['confirmed'],'text_confirmation':match,
             'source_check':{k:v for k,v in page.items() if k!='extracted_text'},'manual_source':True}
        report['suggested_sources']=[r for r in report.get('suggested_sources',[]) if r.get('candidate_id')!=ref['candidate_id']]+[ref]
        if match['confirmed']:compare(row,ref,report)
        elif not report.get('references'):report['state']='needs_confirmation' if text else 'source_unavailable'
    elif action=='confirm':
        ref=next((r for r in report.get('suggested_sources',[])+report.get('references',[]) if r.get('candidate_id')==args.get('candidate_id')),None)
        if not ref or not ref.get('plain_lyrics'):raise ValueError('Сначала загрузите или вставьте текст источника')
        ref=copy.deepcopy(ref);ref.update(text_confirmed=True,user_confirmed=True)
        compare(row,ref,report)
    elif action in {'apply', 'apply_all'}:
        indexes=args.get('indexes')
        if action=='apply_all':
            indexes=[i for i,item in enumerate(report.get('candidates',[]))
                     if not item.get('auto_apply') and item.get('status')!='user_applied']
        if not isinstance(indexes,list) or not indexes or any(type(i)!=int or not 0<=i<len(report.get('candidates',[])) for i in indexes):
            raise ValueError('Выберите исправления для применения')
        phrases=copy.deepcopy(row['phrases']);histories=copy.deepcopy(row.get('phrase_history',{}));chords=copy.deepcopy(row.get('chords',[]));count=0
        for phrase in phrases:
            changes=[report['candidates'][i] for i in set(indexes) if report['candidates'][i]['current']['phrase_id']==phrase['phrase_id']]
            if not changes:continue
            before=copy.deepcopy(phrase); edge=len(phrase['text'])
            for item in sorted(changes,key=lambda i:i['current']['char_start'],reverse=True):
                c=item['current'];a,b=c['char_start'],c['char_end'];new=item['alternative']['text']
                if item.get('status')=='user_applied' or b>edge or phrase['text'][a:b]!=c['text']:
                    raise ValueError('Исправления пересекаются или текст уже изменён — повторите сверку')
                edit_span(phrase,a,b,new);edge=a
                for chord in chords:
                    anchor=chord.get('anchor') or {}; at=anchor.get('grapheme_index')
                    if anchor.get('phrase_id')==phrase['phrase_id'] and isinstance(at,int):
                        if at>=b:anchor['grapheme_index']+=len(new)-(b-a)
                        elif at>=a:anchor['grapheme_index']=a
                item['status']='user_applied';count+=1
            history=histories.setdefault(phrase['phrase_id'],{'versions':[before],'cursor':0})
            history['versions']=history['versions'][:history['cursor']+1]+[copy.deepcopy(phrase)];history['cursor']+=1
        report['user_applied']=True;report['user_applied_count']=report.get('user_applied_count',0)+count
        patch=result_patch(row,report)
        patch.update(phrases=phrases,words=[w for p in phrases for w in p.get('words',[])],chords=chords,phrase_history=histories)
        return store.patch(row['song_id'],patch,row['revision'])
    else:raise ValueError('Неизвестное действие с источником текста')
    return store.patch(row['song_id'],result_patch(row,report),row['revision'])
