"""Independent audio confirmation, boundary edits, and no destructive voting."""
import copy
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'core'))
from songs.reconciliation import propose_repairs, confirms, confirm_coverage, apply_repairs, fresh_agreements, boundary_windows, protect_prior_agreement, quarantine_credit_phrases
from songs.consensus import consensus, choose_spine
from songs.worker import repair_disagreements
from songs.analysis import SongAnalyzer


def phrase(text, start=0, end=10000, pid='p'):
    return dict(text=text, start_ms=start, end_ms=end, phrase_id=pid,
                words=[{'text': 'old'}], timing_quality='word_estimated')


class ReconciliationTests(unittest.TestCase):
    def test_unsupported_credit_is_quarantined_with_original_evidence(self):
        p=phrase('Редактор субтитров А.Петров Корректор И.Иванова',180000,195000)
        lyric=phrase('Мне редактор ничего не скажет',150000,160000)
        whisper=dict(model='Whisper',phrases=[lyric,p])
        qwen=dict(model='Qwen3-ASR-1.7B',phrases=[])
        check=dict(start_ms=175000,end_ms=200000,hypotheses=[whisper,qwen])
        kept,excluded=quarantine_credit_phrases([lyric,p],[whisper,qwen],[check])
        self.assertEqual(kept,[lyric])
        self.assertEqual(excluded[0]['phrase'],p)
        self.assertEqual(whisper['phrases'],[lyric,p])
        self.assertEqual(quarantine_credit_phrases([p],[whisper,qwen],[]),([p],[]))

    def test_corroborated_spoken_credits_are_retained(self):
        p=phrase('Субтитры добавил Петров',180000,195000)
        whisper=dict(model='Whisper',phrases=[p])
        qwen=dict(model='Qwen3-ASR-1.7B',phrases=[p])
        check=dict(start_ms=175000,end_ms=200000,hypotheses=[whisper,qwen])
        self.assertEqual(quarantine_credit_phrases([p],[whisper,qwen],[check]),([p],[]))
        empty=dict(qwen,phrases=[])
        self.assertEqual(quarantine_credit_phrases([p],[whisper,empty],[check]),([p],[]))

    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.audio=Path(tmp.name)/'fixture.wav';self.audio.write_bytes(b'fixture-not-audio')
        self.base = [phrase('Даже когда города', end=5000, pid='a'),
                     phrase('Стану смотреть уныло', start=5000, pid='b')]
        self.alt = {'model': 'Whisper-large-v2', 'phrases': [
            phrase('Даже когда города и даты станут смотреть уныло')]}

    def proposals(self):
        return propose_repairs(self.base, [self.alt])

    def test_missing_short_span_across_phrases_is_detected(self):
        p = self.proposals()[0]
        self.assertEqual(p['text'], 'и даты станут')
        self.assertEqual(p['context'], ['когда','города','и','даты','станут','смотреть','уныло'])

    def test_two_independent_audio_votes_required(self):
        proposals = self.proposals()
        proposals[0]['confirmed_models'] = ['Whisper-large-v2', 'Whisper-retry']
        self.assertEqual(apply_repairs(self.base, proposals), self.base)
        proposals[0]['confirmed_models'].append('Qwen3-ASR')
        result = apply_repairs(self.base, proposals)
        self.assertEqual(result[1]['text'], 'и даты станут смотреть уныло')
        self.assertEqual(result[1]['words'], [])
        self.assertEqual(result[1]['timing_quality'], 'phrase')
        self.assertEqual(self.base[1]['text'], 'Стану смотреть уныло')

    def test_partial_or_distant_confirmation_is_rejected(self):
        p = self.proposals()[0]
        self.assertTrue(confirms(p, self.alt))
        self.assertFalse(confirms(p, {'phrases':[phrase('и даты станут')]}))
        self.assertFalse(confirms(p, {'phrases':[phrase(self.alt['phrases'][0]['text'],60000,70000)]}))

    def test_repeated_chorus_cannot_supply_missing_text(self):
        distant = copy.deepcopy(self.alt)
        distant['phrases'][0].update(start_ms=60000, end_ms=70000)
        self.assertEqual(propose_repairs(self.base, [distant]), [])
        result = consensus(self.base,[distant],choose_majority=True)
        self.assertTrue(all(v['agree']==1 for p in result for v in p['agreement']))

    def test_unanchored_hallucinated_intro_is_not_inserted(self):
        hyp = {'model':'qwen', 'phrases':[phrase('Титры и подписка Даже когда города Стану смотреть уныло')]}
        self.assertEqual(propose_repairs(self.base,[hyp]), [])

    def test_shorter_hypothesis_cannot_delete_text(self):
        hyp = {'model':'qwen', 'phrases':[phrase('Даже когда смотреть уныло')]}
        self.assertEqual(propose_repairs(self.base,[hyp]), [])

    def test_cross_phrase_edit_preserves_both_tails(self):
        base = [phrase('Первая часть обрыв', end=5000),phrase('слова вторая часть',start=5000,pid='q')]
        alt = {'model':'qwen','phrases':[phrase('Первая часть целое слово вторая часть')]}
        p = propose_repairs(base,[alt])
        p[0]['confirmed_models']=['whisper','qwen']
        result = apply_repairs(base,p)
        self.assertEqual([q['text'] for q in result],['Первая часть целое слово вторая часть'])
        self.assertEqual((result[0]['start_ms'], result[0]['end_ms']),(0,10000))

    def test_recheck_runs_two_decoders_and_preserves_evidence(self):
        base={'model':'gigaam','phrases':self.base}
        qwen=dict(self.alt,model='Qwen3-ASR')
        with patch('songs.worker.whisper_transcribe',return_value=self.alt), patch(
                'songs.worker.qwen_transcribe',return_value=qwen):
            rows,report=repair_disagreements(self.audio,base,[base,self.alt,qwen],lambda _:None)
        self.assertEqual(report['accepted'],1)
        self.assertEqual(len(report['rechecks'][0]['hypotheses']),2)
        self.assertIn('и даты станут',rows[1]['text'])

    def test_single_model_does_not_load_extra_models(self):
        with patch('songs.worker.whisper_transcribe',side_effect=AssertionError('unexpected model')):
            rows,report=repair_disagreements('test.wav',self.alt,[self.alt],lambda _:None)
        self.assertEqual(rows,self.alt['phrases'])
        self.assertEqual(report['rechecks'],[])

    def test_default_pair_and_explicit_selection(self):
        self.assertEqual(SongAnalyzer.normalize_models(None),('whisper','qwen_large'))
        self.assertEqual(SongAnalyzer.normalize_models(['gigaam']),('gigaam',))
        self.assertEqual(SongAnalyzer.normalize_models(['whisper','qwen_large']),('whisper','qwen_large'))
        with self.assertRaises(ValueError):SongAnalyzer.normalize_models(['qwen','qwen_large'])

    def test_larger_qwen_rechecks_selected_version_and_reuses_only_whisper(self):
        base={'model':'Whisper-large-v2','phrases':self.base}
        small=dict(self.alt,model='Qwen3-ASR-0.6B')
        large=dict(self.alt,model='Qwen3-ASR-1.7B')
        with patch('songs.worker.whisper_transcribe',return_value=self.alt),patch(
                'songs.worker.qwen_transcribe',return_value=small):
            _,prior=repair_disagreements(self.audio,base,[base,small],lambda _:None)
        with patch('songs.worker.whisper_transcribe',side_effect=AssertionError('cached')),patch(
                'songs.worker.qwen_transcribe',return_value=large) as qwen:
            _,report=repair_disagreements(self.audio,base,[base,large],lambda _:None,previous=prior)
        self.assertTrue(qwen.call_args_list)
        self.assertTrue(all(c.kwargs.get('model_repo')=='Qwen/Qwen3-ASR-1.7B' for c in qwen.call_args_list))
        self.assertTrue(all(h['model']!='Qwen3-ASR-0.6B' for r in report['rechecks'] for h in r['hypotheses']))

    def test_missing_opening_needs_fresh_pair_not_old_text_or_alignment(self):
        base=[phrase('Ошибочные титры',end=1000)]
        a={'model':'Whisper', 'phrases':[phrase('Настоящая первая строка с полным началом песни',end=25000)]}
        b=dict(a,model='Qwen3-ASR')
        proposals=propose_repairs(base,[b])
        self.assertTrue(proposals[0]['coverage'])
        self.assertEqual(apply_repairs(base,proposals),base)
        confirm_coverage(proposals[0],[a,b])
        repaired=apply_repairs(base,proposals)
        self.assertEqual(repaired[0]['text'],a['phrases'][0]['text'])
        self.assertEqual(repaired[0]['end_ms'],25000)

    def test_matching_first_words_do_not_hide_a_mostly_missing_opening(self):
        base=[phrase('Я с тобой не боюсь',end=5000)]
        text='Я с тобой не боюсь ни дождя ни грозы если с края сорвусь'
        p=propose_repairs(base,[dict(model='Qwen',phrases=[phrase(text,end=25000)])])
        self.assertEqual(len(p),1)
        self.assertTrue(p[0]['coverage'])
        self.assertEqual(apply_repairs(base,p),base)
        common=' '.join('слово'+str(i) for i in range(30))
        whisper=dict(model='Whisper',phrases=[*base,phrase(common,30000,50000)])
        qwen=dict(model='Qwen3-ASR-1.7B',phrases=[phrase(text,0,25000),phrase(common,30000,50000)])
        self.assertIs(choose_spine([whisper,qwen]),qwen)

    def test_failed_decoder_preserves_original_and_reports_error(self):
        base={'model':'gigaam','phrases':self.base}
        qwen=dict(self.alt,model='Qwen3-ASR')
        with patch('songs.worker.whisper_transcribe',return_value=self.alt), patch(
                'songs.worker.qwen_transcribe',side_effect=RuntimeError('inference failed')):
            rows,report=repair_disagreements(self.audio,base,[base,self.alt,qwen],lambda _:None)
        self.assertEqual(rows,self.base)
        self.assertEqual(report['accepted'],0)
        self.assertEqual(report['errors'][0]['model'],'qwen')

    def test_two_model_spine_keeps_opening_when_rest_of_song_agrees(self):
        common=' '.join('word'+str(i) for i in range(30))
        whisper={'model':'Whisper','phrases':[phrase('ложные титры',end=1000),
            phrase(common,start=20000,end=50000)]}
        qwen={'model':'Qwen3-ASR','phrases':[phrase('первая полная строка без потерянных слов в начале',end=19000),
            phrase(common,start=20000,end=50000)]}
        self.assertIs(choose_spine([whisper,qwen]),qwen)

    def test_multiple_repairs_in_same_phrase_are_all_applied(self):
        rows=[phrase('начало плохой середина неверный конец')]
        proposals=[dict(begin=1,end=2,text='хороший',confirmed_models=['whisper','qwen']),
                   dict(begin=3,end=4,text='верный',confirmed_models=['whisper','qwen'])]
        result=apply_repairs(rows,proposals)
        self.assertEqual(result[0]['text'],'начало хороший середина верный конец')
        self.assertEqual(len(result[0]['audio_repairs']),2)

    def test_completed_windows_resume_without_inference(self):
        base={'model':'gigaam','phrases':self.base};qwen=dict(self.alt,model='Qwen3-ASR')
        with patch('songs.worker.whisper_transcribe',return_value=self.alt),patch(
                'songs.worker.qwen_transcribe',return_value=qwen):
            original,report=repair_disagreements(self.audio,base,[base,self.alt,qwen],lambda _:None)
        with patch('songs.worker.whisper_transcribe',side_effect=AssertionError('cached')),patch(
                'songs.worker.qwen_transcribe',side_effect=AssertionError('cached')):
            restored,resumed=repair_disagreements(self.audio,base,[base,self.alt,qwen],lambda _:None,previous=report)
        self.assertEqual(original,restored)
        self.assertEqual(resumed['errors'],[])

    def test_no_eight_window_cutoff(self):
        base={'model':'Whisper','phrases':self.base};qwen=dict(self.alt,model='Qwen3-ASR')
        proposals=[dict(begin=0,end=1,text='x',context=['x'],start_ms=i*20000,end_ms=i*20000+5000,
                        status='needs_review') for i in range(10)]
        with patch('songs.reconciliation.propose_repairs',return_value=proposals),patch(
                'songs.worker.whisper_transcribe',return_value={'model':'Whisper','phrases':[]}),patch(
                'songs.worker.qwen_transcribe',return_value={'model':'Qwen','phrases':[]}):
            _,report=repair_disagreements(self.audio,base,[base,qwen],lambda _:None)
        self.assertGreaterEqual(len(report['rechecks']),20)
        self.assertTrue(any(r['start_ms']==180000 for r in report['rechecks']))

    def test_new_variant_from_fresh_pair_can_repair_both_initial_errors(self):
        base=[phrase('Мы видим плохой текст и сохраняем')]
        evidence=[dict(model=name,phrases=[phrase('Мы видим полный текст и сохраняем')])
                  for name in ('Whisper','Qwen')]
        proposals=fresh_agreements(base,[dict(start_ms=0,end_ms=10000,hypotheses=evidence)])
        self.assertEqual(len(proposals),1)
        self.assertTrue(proposals[0]['discovered_on_recheck'])
        self.assertEqual(apply_repairs(base,proposals)[0]['text'],'Мы видим полный текст и сохраняем')

    def test_crop_agreement_cannot_replace_word_both_full_models_already_agree_on(self):
        base={'model':'Whisper','phrases':[phrase('Все слова записаны здесь правильно')]}
        peer=dict(base,model='Qwen')
        proposal=dict(begin=2,end=3,text='запесаны',confirmed_models=['Whisper','Qwen'])
        protect_prior_agreement(base,[base,peer],[proposal])
        self.assertEqual(proposal['status'],'conflicting_audio_evidence')
        self.assertEqual(apply_repairs(base['phrases'],[proposal]),base['phrases'])

    def test_fresh_variant_cannot_combine_votes_from_different_crops(self):
        base=[phrase('Мы видим плохой текст и сохраняем')]
        evidence=[dict(model=name,phrases=[phrase('Мы видим полный текст и сохраняем')])
                  for name in ('Whisper','Qwen')]
        checks=[dict(start_ms=0,end_ms=10000,hypotheses=[h]) for h in evidence]
        self.assertEqual(fresh_agreements(base,checks),[])

    def test_chunk_seams_and_last_words_get_context_even_without_disagreement(self):
        windows=boundary_windows([{'model':'Qwen3-ASR','phrases':[
            phrase('первый',0,25000),phrase('второй',25000,40000)]}])
        self.assertIn((17000,33000),windows)
        self.assertIn((0,16000),windows)
        self.assertIn((24000,40000),windows)

    def test_expanded_crop_cannot_replace_whole_block_with_adjacent_lyrics(self):
        base={'model':'Whisper','phrases':[phrase('Исходная строка')]}
        peer=dict(base,model='Qwen')
        p=dict(begin=0,end=2,text='',context=[],start_ms=0,end_ms=10000,
               source_model='Qwen',status='needs_review',coverage=True)
        good='Соседняя строка потом эта строка потом ещё соседняя'
        def whisper(_audio,_run,start,end):
            return dict(model='Whisper',phrases=[phrase('один' if end==10000 else good)])
        def qwen(_audio,_progress,windows):
            return dict(model='Qwen',phrases=[phrase('другой' if windows[0][1]==10000 else good)])
        with patch('songs.reconciliation.propose_repairs',return_value=[p]),patch(
                'songs.reconciliation.boundary_windows',return_value=[]),patch(
                'songs.reconciliation.fresh_agreements',return_value=[]),patch(
                'songs.worker.whisper_transcribe',side_effect=whisper),patch(
                'songs.worker.qwen_transcribe',side_effect=qwen):
            rows,report=repair_disagreements(self.audio,base,[base,peer],lambda _:None)
        self.assertEqual(report['accepted'],0)
        self.assertEqual(rows,base['phrases'])


if __name__ == '__main__':
    unittest.main()
