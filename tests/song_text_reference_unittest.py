"""Production post-ASR source stage: isolated fixtures, no network/models/settings."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.reference_text import tokens
from songs.reference_fragments import anchors, refresh_agreement
from songs.text_reference import run, confirm_text, reconcile, result_patch
from songs.store import SongStore
from songs.worker import verify_reference_candidate
from songs import worker

LINES=['Вечерний поезд медленно уходит за деревню',
       'Тихий ветер согревает окна нашего дома',
       'Ранним утром золотые листья падают на землю']

def phrases():
    return [dict(phrase_id=str(i),text=line,start_ms=i*10000,end_ms=(i+1)*10000,words=[],
        agreement=[dict(char_start=t['start'],char_end=t['end'],text=t['text'],agree=2,models=2,
                        alternatives=[t['text']],needs_review=False) for t in tokens(line)]) for i,line in enumerate(LINES)]

def page(text, chords=False):
    return dict(status='available',extracted_text=text,page_title='Публикация',chords_extractable=chords,chord_names=['Am','E7'] if chords else [])

class TextReferenceTests(unittest.TestCase):
    def test_machine_agreement_does_not_protect_disputed_span(self):
        p=dict(phrase_id='tail',text='У меня для тебя есть секрет, мы греметки.',start_ms=100,end_ms=9000,
               agreement=[dict(char_start=21,char_end=28,agree=2,models=2)])
        refs=[dict(plain_lyrics='У меня для тебя есть секретные метки',url='https://example.org/text')]
        with patch('songs.text_reference._protected',wraps=__import__('songs.text_reference',fromlist=['_protected'])._protected):
            updated,items=reconcile([p],refs,lambda *args:{'confirmed':True})
        self.assertEqual(updated[0]['text'],'У меня для тебя есть секретные метки.')
        self.assertTrue(items[0]['auto_apply'])
        p['review_state']='verified'
        updated,items=reconcile([p],refs,lambda *args:self.fail('Human verification must survive'))
        self.assertEqual(updated,[p])

    def test_phrase_edge_and_local_audio_window(self):
        p=dict(text='У меня для тебя есть секрет, мы греметки.',start_ms=109000,end_ms=126720,
               words=[dict(char_start=11,char_end=15,start_ms=122800,end_ms=123200),
                      dict(char_start=16,char_end=20,start_ms=123920,end_ms=124480),
                      dict(char_start=21,char_end=28,start_ms=124480,end_ms=124960),
                      dict(char_start=32,char_end=41,start_ms=125120,end_ms=126720)])
        item=dict(current=dict(char_start=21,char_end=40,text='секрет, мы греметки'),
                  alternative=dict(text='секретные метки'),source_confirmed=True)
        hyp=lambda m,t:dict(model=m,phrases=[dict(text=t)])
        hs=[hyp('Whisper-large-v2',p['text']),hyp('Qwen3-ASR-1.7B','У меня для тебя есть секретные метки')]
        with patch('songs.worker.whisper_transcribe',return_value=hs[0]) as w,patch('songs.worker.qwen_transcribe',return_value=hs[1]) as q:
            result=verify_reference_candidate('audio',p,item,hs,lambda _:None)
            self.assertTrue(result['confirmed']);self.assertEqual(result['basis'],'source_and_audio')
            self.assertEqual(w.call_args.args[2:],(120800,128720))
            self.assertNotIn('prompt',q.call_args.kwargs)
            item['source_confirmed']=False
            self.assertFalse(verify_reference_candidate('audio',p,item,hs,lambda _:None)['confirmed'])

    def test_source_tie_breaker_does_not_change_meaning_or_count_same_model_twice(self):
        from songs.worker import reference_audio_decision
        ev=[dict(model='Whisper-large-v2',matches=False,matches_current=True),
            dict(model='Qwen3-ASR-1.7B',matches=True,matches_current=False)]
        check=lambda a,b,e=ev:reference_audio_decision({'source_confirmed':True},a.split(),b.split(),e,0,9000)['confirmed']
        self.assertTrue(check('секрет мы греметки','секретные метки'))
        self.assertFalse(check('не вернется','нам вернется'))
        self.assertFalse(check('секрет мы греметки','секретные метки',[ev[1],ev[1]]))
        self.assertFalse(check('слова в старой версии','совсем другие слова'))

    def test_new_boundary_reading_is_compared_between_the_same_context(self):
        from songs.worker import reference_boundary_reading,reference_audio_decision
        reading=reference_boundary_reading('для тебя есть секрет умные метки выдох вдох'.split(),
                                          ['тебя','есть'],[],['секретные','метки'])
        self.assertEqual(reading['boundary_reading'],['секрет','умные','метки'])
        self.assertGreater(reading['boundary_similarity'],.9)
        ev=[dict(model='Whisper-large-v2',matches=False,**reading),dict(model='Qwen3-ASR-1.7B',matches=True)]
        result=reference_audio_decision({'source_confirmed':True},['секрет','мы','греметки'],['секретные','метки'],ev,0,9000)
        self.assertEqual(result['basis'],'source_and_audio')
        wrong=reference_boundary_reading('где-то в другой песне секрет умные метки'.split(),['тебя','есть'],[],['секретные','метки'])
        self.assertEqual(wrong['boundary_similarity'],0)

    def test_legacy_empty_model_is_not_a_vote_and_display_text_is_not_a_model(self):
        ps=phrases()
        hypotheses=[{'model':'not-selected','phrases':[]},
                    {'model':'Whisper-large-v2','phrases':ps},
                    {'model':'Qwen3-ASR-1.7B','phrases':ps}]
        updated=refresh_agreement(ps,hypotheses)
        self.assertEqual(len(anchors(updated)),3)
        for p in updated:
            self.assertTrue(all(a['agree']==a['models']==2 for a in p['agreement']))
        one=refresh_agreement(ps,hypotheses[:2])
        self.assertEqual(anchors(one),[])
        manual=copy.deepcopy(ps)
        manual[0]['text']='Совершенно другая фраза после ручной правки'
        refreshed=refresh_agreement(manual,hypotheses)
        self.assertEqual(refreshed[0]['text'],manual[0]['text'])
        self.assertFalse(any(a['phrase_id']=='0' for a in anchors(refreshed)))
        self.assertEqual(ps,phrases())

    def test_detached_worker_orders_reference_before_alignment_and_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'result.json'
            ps=phrases()
            hypotheses=[{'model':m,'phrases':ps} for m in ['Whisper-large-v2','Qwen3-ASR-1.7B']]
            out.write_text(json.dumps({'hypotheses':hypotheses,'errors':[]}))
            order=[]
            def repair(*args,**kwargs):order.append('consensus');return ps,{}
            from songs.text_reference import run as actual_run
            def reference(*args,**kwargs):
                order.append('reference')
                return actual_run(*args,**kwargs,search=lambda q:[{'url':'https://example.org/a'}],fetch=lambda u:page('\n'.join(LINES)))
            def align(audio,selected,*args,**kwargs):
                order.append('align');self.assertEqual(selected['phrases'],ps)
            def structure(phrases):order.append('structure');return []
            with patch.object(sys,'argv',['worker','--input',str(Path(tmp)/'unused.wav'),'--output',str(out),'--resume','--steps','consensus,text_reference,align,structure']), patch('songs.worker.repair_disagreements',side_effect=repair), patch('songs.text_reference.run',side_effect=reference), patch('songs.worker.align_phrases',side_effect=align), patch('songs.worker.align_intro_from_vocals'), patch('songs.structure.infer_sections',side_effect=structure):
                worker.main()
            data=json.loads(out.read_text())
            self.assertEqual(order,['consensus','reference','align','structure'])
            self.assertTrue(data['completed']);self.assertEqual(data['text_reference']['state'],'compared')

    def test_three_exact_independent_anchors_required(self):
        selected=anchors(phrases())
        self.assertTrue(confirm_text('\n'.join(LINES),selected)['confirmed'])
        self.assertFalse(confirm_text('\n'.join(LINES[:2]),selected)['confirmed'])
        self.assertFalse(confirm_text('\n'.join([LINES[0]]*3),selected)['confirmed'])
        p=phrases();p[0]['agreement'][0].update(models=3)
        self.assertNotIn(LINES[0], [a['text'] for a in anchors(p)])
        p=phrases()
        for x in p:
            for a in x['agreement']:a.update(models=3)
        self.assertEqual(anchors(p),[])

    def test_fetched_body_not_search_snippet_confirms_and_two_sources(self):
        queries=[];visited=[]
        def search(q):
            queries.append(q)
            return [dict(url='https://example.org/'+str(i),snippet='\n'.join(LINES)) for i in range(3)]
        def fetch(url):
            visited.append(url)
            return page('Нет такого текста' if url.endswith('0') else '\n'.join(LINES)+'\nКонец '+url[-1],chords=True)
        before=phrases();updated,report=run(before,{'title':'Неправильное имя'},search=search,fetch=fetch)
        self.assertEqual(updated,before)
        self.assertEqual(report['state'],'compared');self.assertEqual(len(report['references']),2)
        self.assertNotIn('Неправильное',queries[0]);self.assertTrue(queries[0].startswith('"'))
        self.assertTrue(any('аккорды' in q for q in queries))
        self.assertEqual(len(report['chord_sources']),2)
        self.assertEqual(len(visited),len(set(visited)))
        self.assertTrue(all(len(r['text_confirmation']['hits'])>=3 for r in report['references']))

    def test_no_consensus_can_search_but_missing_source_never_changes_text(self):
        def forbidden(*a):raise AssertionError('Network must not run')
        p=phrases()[:2]
        queries=[]
        updated,report=run(p,search=lambda q:queries.append(q) or [],fetch=forbidden)
        self.assertEqual(updated,p);self.assertEqual(report['state'],'insufficient_consensus')
        self.assertTrue(queries)
        updated,report=run(phrases(),search=lambda q:[],fetch=forbidden)
        self.assertEqual(updated,phrases());self.assertEqual(report['state'],'not_found')

    def test_broken_page_is_not_a_source(self):
        updated,report=run(phrases(),search=lambda q:[{'url':'https://example.org/404'}],
                           fetch=lambda u:{'status':'unavailable','http_status':404})
        self.assertEqual(report['references'],[]);self.assertEqual(updated,phrases())

    def test_audio_confirmation_preserves_other_text_and_times(self):
        p=dict(phrase_id='bad',text='Вчера он сунул с дурука бели на две ноги в штанину',start_ms=100,end_ms=9000,
               words=[{'text':'Вчера','start_ms':100,'end_ms':500}])
        refs=[dict(plain_lyrics='Вчера он сунул сдуру, кобелина две ноги в штанину',url='https://example.org/a')]
        before=copy.deepcopy(p)
        unchanged,proposals=reconcile([p],refs,lambda *a:{'confirmed':False})
        self.assertEqual(unchanged,[before]);self.assertFalse(proposals[0]['auto_apply'])
        updated,proposals=reconcile([p],refs,lambda *a:{'confirmed':True})
        self.assertEqual(updated[0]['text'],refs[0]['plain_lyrics'])
        self.assertEqual(updated[0]['start_ms'],100);self.assertEqual(updated[0]['end_ms'],9000)
        self.assertEqual(updated[0]['words'],[]);self.assertTrue(updated[0]['alignment_stale'])
        self.assertEqual(updated[0]['text_before_reference'],before['text']);self.assertEqual(p,before)
        p['manual_edited']=True
        updated,proposals=reconcile([p],refs,lambda *a:(_ for _ in ()).throw(AssertionError('Must not verify manual edits')))
        self.assertEqual(updated,[p]);self.assertEqual(proposals[0]['status'],'protected_text')

    def test_reference_check_needs_two_unprompted_audio_matches(self):
        p={'text':'Вчера он нагорел меня вечером','start_ms':0,'end_ms':6000}
        proposal={'current':{'char_start':9,'char_end':16},'alternative':{'text':'нагрел'}}
        hyp=lambda model,text:{'model':model,'phrases':[{'text':text}]}
        h=[hyp('Whisper-large-v2',''),hyp('Qwen3-ASR-1.7B','')]
        with patch('songs.worker.whisper_transcribe',return_value=hyp('Whisper-large-v2','Вчера он нагрел меня вечером')) as w, patch('songs.worker.qwen_transcribe',return_value=hyp('Qwen3-ASR-1.7B','Вчера он нагорел меня вечером')) as q:
            self.assertFalse(verify_reference_candidate('audio',p,proposal,h,lambda _:None)['confirmed'])
            q.return_value=hyp('Qwen3-ASR-1.7B','Вчера он нагрел меня вечером')
            self.assertTrue(verify_reference_candidate('audio',p,proposal,h,lambda _:None)['confirmed'])
            self.assertNotIn('prompt',q.call_args.kwargs);self.assertNotIn('text',w.call_args.kwargs)

    def test_save_and_rerun_report_preserve_recording_and_manual_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SongStore(Path(tmp)/'songs');row=store.create('Test')
            report={'state':'compared','references':[dict(candidate_id='text-reference:1',kind='lyrics',url='https://example.org/poem',plain_lyrics='\n'.join(LINES))]}
            row=store.patch(row['song_id'],dict(status='running',active_run_id='run',source_ref='https://example.org/recording'))
            saved=store.save_full_analysis(row['song_id'],'run',phrases(),'test',dict(selected={'phrases':phrases()},text_reference=report))
            self.assertEqual(saved['text_reference'],report)
            self.assertEqual(saved['analysis_runs'][-1]['text_reference'],report)
            self.assertEqual(saved['source_ref'],row['source_ref'])
            self.assertEqual(saved['source_candidates'][0]['url'],'https://example.org/poem')
            cleaned=result_patch(saved,{'state':'not_found','references':[]})
            self.assertEqual(cleaned['source_candidates'],[])

if __name__=='__main__':unittest.main()
