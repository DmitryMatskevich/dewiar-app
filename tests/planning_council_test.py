"""Exact-version consensus, resume limits, cancellation and task model bindings."""
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import planning as P
import tasks

PLAN = '# План\n**Готово, когда:** исправлен add, реальные тесты проходят.\n- [ ] Исправить add\n- [ ] Запустить тесты'
MEMBERS = [{'id':'a','name':'A'},{'id':'b','name':'B'}]
JUDGE = {'id':'judge','name':'Judge'}


class Council(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'совет.json'; self.cancel = threading.Event()

    def vote(self,msgs,verdict='approve',plan='',notes=''):
        h = re.search(r'plan_hash: ([a-f0-9]{64})',msgs[0]['content'])[1]
        return json.dumps({'verdict':verdict,'plan_hash':h,'plan':plan,'blocking_issues':[],'notes':notes})

    def discuss(self,ask,**kwargs):
        return P.discuss(self.path,MEMBERS,'Исправить add',ask,self.cancel,**kwargs)

    def test_all_members_approve_same_version_and_see_previous_critique(self):
        seen = []
        def ask(member,system,msgs,deadline):
            seen.append((member['id'],msgs[0]['content']))
            return self.vote(msgs,'revise',PLAN,'Нужны тесты границ') if len(seen)==1 else self.vote(msgs)
        state = self.discuss(ask)
        self.assertEqual(state['status'],'agreed'); self.assertEqual(state['calls'],3)
        self.assertIn('Нужны тесты границ',seen[1][1])
        self.assertEqual(set(state['votes'].values()),{P.plan_hash(PLAN)})

    def test_revision_invalidates_prior_approval(self):
        def ask(member,system,msgs,deadline):
            n = json.loads(self.path.read_text())['calls']
            return self.vote(msgs,'revise',PLAN+'\n- [ ] Проверить ноль') if n==2 else self.vote(msgs)
        state = self.discuss(ask,seed=PLAN)
        self.assertEqual(state['status'],'agreed')
        self.assertEqual(state['calls'],4)  # A must approve the changed plan, then B too.

    def test_no_fake_consensus_on_round_limit_or_resume(self):
        ask = Mock(side_effect=lambda m,s,msgs,d:self.vote(msgs,'revise',PLAN+f'\n# {m["id"]}'))
        state = self.discuss(ask,max_rounds=1)
        self.assertEqual(state['status'],'no_consensus')
        self.assertEqual(ask.call_count,2)
        again = self.discuss(ask,max_rounds=1)
        self.assertEqual(again['calls'],2); self.assertEqual(ask.call_count,2)

    def test_empty_and_wrong_version_votes_do_not_count(self):
        for raw in ('{}','not-json',json.dumps({'verdict':'approve','plan_hash':'old'})):
            P.reset(self.path)
            state = self.discuss(lambda *args:raw,seed=PLAN,max_rounds=1)
            self.assertEqual(state['status'],'no_consensus'); self.assertEqual(state['votes'],{})

    def test_approve_cannot_smuggle_new_plan_or_blocking_issue(self):
        def ask(member,system,msgs,deadline):
            v = json.loads(self.vote(msgs)); v['blocking_issues']=['Не хватает теста']
            return json.dumps(v)
        self.assertEqual(self.discuss(ask,seed=PLAN,max_rounds=1)['status'],'no_consensus')
        P.reset(self.path)
        self.assertEqual(self.discuss(lambda m,s,msgs,d:self.vote(msgs,plan='Другой план'),seed=PLAN,max_rounds=1)['status'],'no_consensus')

    def test_unavailable_member_is_not_replaced(self):
        def ask(*args): raise P.TeamPaused('Модель A исчерпала лимит')
        state = self.discuss(ask)
        self.assertEqual(state['status'],'blocked'); self.assertEqual(state['calls'],1)

    def test_cancellation_and_elapsed_budget(self):
        self.cancel.set(); ask = Mock()
        self.assertEqual(self.discuss(ask)['status'],'cancelled'); ask.assert_not_called()
        self.cancel.clear(); P.reset(self.path)
        self.assertEqual(self.discuss(ask,budget_seconds=0)['status'],'budget_exhausted'); ask.assert_not_called()

    def test_late_approval_is_not_consensus(self):
        clock = [0.]
        def ask(member,system,msgs,deadline):
            clock[0] = 10
            return self.vote(msgs)
        with patch('planning.time.monotonic',side_effect=lambda:clock[0]):
            state = self.discuss(ask,seed=PLAN,budget_seconds=1)
        self.assertEqual(state['status'],'budget_exhausted')
        self.assertEqual(state['votes'],{})

    def test_changed_members_cannot_reuse_consensus(self):
        self.discuss(lambda m,s,msgs,d:self.vote(msgs),seed=PLAN)
        with self.assertRaises(P.TeamPaused):
            P.discuss(self.path,[{'id':'a','name':'Changed'},{'id':'b','name':'B'}],'Исправить add',Mock(),self.cancel)

    def judgment(self,msgs,verdict='complete',issues=None):
        return json.dumps({'verdict':verdict,'plan_hash':re.search(r'plan_hash: ([a-f0-9]{64})',msgs[0]['content'])[1],
                           'unresolved_issues':issues or [],'reason':'Проверены требования и закрытие замечаний'})

    def test_judge_runs_only_after_unanimity_and_sees_debate(self):
        seen = []
        def ask(m,s,msgs,d):
            seen.append(m['id'])
            if m['id']=='judge':
                self.assertIn('ПРОТОКОЛ:',msgs[0]['content'])
                self.assertIn('КОНТЕКСТ ПРОЕКТА:',msgs[0]['content'])
                return self.judgment(msgs)
            return self.vote(msgs)
        state = self.discuss(ask,seed=PLAN,judge=JUDGE)
        self.assertEqual(seen,['a','b','judge'])
        self.assertEqual(state['status'],'agreed'); self.assertEqual(state['judge_calls'],1)
        self.assertEqual(state['evaluation']['verdict'],'complete')
        self.discuss(Mock(side_effect=AssertionError('Paid duplicate')),seed=PLAN,judge=JUDGE)

    def test_judge_objections_return_to_members_and_need_new_approvals(self):
        seen = []; judgments = []
        def ask(m,s,msgs,d):
            seen.append(m['id'])
            if m['id']=='judge':
                judgments.append(1)
                return self.judgment(msgs,'continue',['Не описана проверка нуля']) if len(judgments)==1 else self.judgment(msgs)
            if len(judgments)==1:
                self.assertIn('Не описана проверка нуля',msgs[0]['content'])
            return self.vote(msgs)
        state = self.discuss(ask,seed=PLAN,judge=JUDGE)
        self.assertEqual(state['status'],'agreed')
        self.assertEqual(seen,['a','b','judge','a','b','judge'])
        self.assertEqual(state['calls'],4)

    def test_bad_or_unavailable_judge_never_allows_development(self):
        for result in ('garbage','{}',json.dumps({'verdict':'complete','plan_hash':'wrong'})):
            P.reset(self.path)
            state = self.discuss(lambda m,s,msgs,d: result if m['id']=='judge' else self.vote(msgs),seed=PLAN,judge=JUDGE)
            self.assertEqual(state['status'],'blocked')
        P.reset(self.path)
        def ask(m,s,msgs,d):
            if m['id']=='judge': raise P.TeamPaused('Лимит оценщика')
            return self.vote(msgs)
        self.assertEqual(self.discuss(ask,seed=PLAN,judge=JUDGE)['status'],'blocked')

    def test_judge_cannot_complete_with_unresolved_issues_or_wrong_hash(self):
        for mutate in (lambda r:r.update(unresolved_issues=['Нет тестов']),lambda r:r.update(plan_hash='old'),lambda r:r.update(reason='')):
            P.reset(self.path)
            def ask(m,s,msgs,d):
                if m['id']!='judge': return self.vote(msgs)
                r=json.loads(self.judgment(msgs)); mutate(r); return json.dumps(r)
            self.assertEqual(self.discuss(ask,seed=PLAN,judge=JUDGE)['status'],'blocked')

    def test_judge_limits_are_persisted_and_changed_judge_cannot_reuse_result(self):
        def ask(m,s,msgs,d):
            return self.judgment(msgs,'continue',['Остаётся вопрос']) if m['id']=='judge' else self.vote(msgs)
        state = self.discuss(ask,seed=PLAN,judge=JUDGE,max_rounds=1)
        self.assertEqual(state['status'],'no_consensus'); self.assertEqual(state['judge_calls'],1)
        again = Mock(side_effect=AssertionError('Budget reset'))
        self.discuss(again,seed=PLAN,judge=JUDGE,max_rounds=1); again.assert_not_called()
        with self.assertRaises(P.TeamPaused): self.discuss(ask,judge=dict(JUDGE,id='another'))

    def test_late_judge_does_not_complete(self):
        clock = [0.]
        def ask(m,s,msgs,d):
            if m['id']=='judge': clock[0]=10; return self.judgment(msgs)
            return self.vote(msgs)
        with patch('planning.time.monotonic',side_effect=lambda:clock[0]):
            state=self.discuss(ask,seed=PLAN,judge=JUDGE,budget_seconds=1)
        self.assertEqual(state['status'],'budget_exhausted'); self.assertFalse(state['evaluation'])

    def test_marking_done_does_not_change_plan_version(self):
        self.assertEqual(P.plan_hash(PLAN),P.plan_hash(PLAN.replace('[ ]','[x]')))
        self.assertNotEqual(P.plan_hash('Код: items[ ]'),P.plan_hash('Код: items[x]'))

    def test_role_bindings_do_not_inherit_automatic_routing(self):
        manager = tasks.TaskManager.__new__(tasks.TaskManager)
        profile = {'kind':'openai','base_url':'http://localhost:5000/v1','model':'coder','name':'Coder'}
        manager.host = SimpleNamespace(s=SimpleNamespace(get=lambda *args:{'coder':profile}),llm_profile=Mock())
        t = SimpleNamespace(data={'team':{'enabled':True,'developer':'coder'}})
        p = manager._role_profile(t,'developer')
        self.assertEqual(p['model'],'coder'); self.assertNotIn('_route_candidates',p)
        manager.host.llm_profile.assert_not_called()

    def test_default_evaluator_is_active_at_start_and_pinned_on_resume(self):
        profiles = {pid:{'kind':'openai','base_url':'http://localhost:5000/v1','model':pid} for pid in ('a','b','active','next')}
        selected = ['active']
        manager = tasks.TaskManager.__new__(tasks.TaskManager)
        settings = SimpleNamespace(get=lambda *args:profiles if args==('llm','profiles') else selected[0],
                                   active_llm=lambda:dict(profiles[selected[0]],_profile_id=selected[0]))
        manager.host = SimpleNamespace(s=settings,emit=Mock(),llm_profile=Mock(side_effect=AssertionError('Task model must not select evaluator')))
        team = {'enabled':True,'planners':['a','b'],'max_rounds':2,'budget_seconds':120}
        task = tasks.Task(Path(self.tmp.name),{'id':'t','title':'T','goal':'G','project':'','team':team,'status':'running'})
        def reply(t,pid,system,msgs,deadline):
            return self.judgment(msgs) if 'Ты оценщик' in system else self.vote(msgs)
        manager._team_ask = Mock(side_effect=reply)
        self.assertTrue(manager._ensure_council(task,seed=PLAN))
        self.assertEqual(task.data['council']['judge']['id'],'active')
        selected[0]='next'; manager._team_ask.reset_mock()
        self.assertTrue(manager._ensure_council(task))
        self.assertEqual(task.data['council']['judge']['id'],'active')
        manager._team_ask.assert_not_called()

    def test_two_aliases_of_one_model_are_not_two_voters(self):
        manager = tasks.TaskManager.__new__(tasks.TaskManager)
        profile = {'base_url':'http://localhost:5000/v1','model':'same'}
        manager.host = SimpleNamespace(s=SimpleNamespace(get=lambda *args:{'a':profile,'b':profile}))
        with self.assertRaises(ValueError): manager._validate_team({'enabled':True,'planners':['a','b']})

    def test_code_tools_are_blocked_without_consensus(self):
        manager = tasks.TaskManager.__new__(tasks.TaskManager)
        t = SimpleNamespace(data={'team':{'enabled':True},'council':{'status':'no_consensus'}})
        with self.assertRaises(P.TeamPaused):
            manager._tool_raw(t,'code_write',{'path':'app.py','content':'unapproved'})

    def test_assigned_unavailable_model_is_not_global_fallback(self):
        manager = tasks.TaskManager.__new__(tasks.TaskManager)
        profile = {'kind':'openai','base_url':'http://localhost:5000/v1','model':'coder'}
        manager.host = SimpleNamespace(s=SimpleNamespace(get=lambda *args:{'coder':profile}),llm_profile=Mock())
        t = SimpleNamespace(data={'team':{'enabled':True,'developer':'coder'}})
        with patch('modelrouter.blocked',return_value='Лимит исчерпан'), self.assertRaises(P.TeamPaused):
            manager._role_profile(t,'developer')
        manager.host.llm_profile.assert_not_called()

    def test_model_change_during_response_cannot_supply_a_vote(self):
        profiles = {pid:{'kind':'openai','base_url':'http://localhost:5000/v1','model':pid} for pid in ('a','b')}
        manager = tasks.TaskManager.__new__(tasks.TaskManager)
        manager.host = SimpleNamespace(s=SimpleNamespace(get=lambda *args:profiles),emit=Mock())
        team = {'enabled':True,'planners':['a','b'],'judge':'a','max_rounds':2,'budget_seconds':120}
        task = tasks.Task(Path(self.tmp.name),{'id':'t','title':'T','goal':'G','project':'','team':team,'status':'running'})
        def reply(t,pid,system,msgs,deadline):
            profiles[pid]['model']='changed'
            return self.vote(msgs,'revise',PLAN)
        manager._team_ask = Mock(side_effect=reply)
        self.assertFalse(manager._ensure_council(task))
        self.assertEqual(task.data['council']['status'],'blocked')
        self.assertEqual(task.data['council']['votes'],{})


if __name__ == '__main__': unittest.main()
