"""Routing time boundaries, quotas and no replay after a streamed side effect."""
import datetime as dt
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import modelrouter as route
import llm
import subscriptions
import egress


class Routing(unittest.TestCase):
    def setUp(self):
        route._health.clear(); route._quotas.clear(); route._loaded = True
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch('modelrouter._quota_path', return_value=Path(self.tmp.name)/'quotas.json')
        self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.old = egress.local_only(); egress.set_local_only(False)
        self.addCleanup(egress.set_local_only, self.old)
        self.a = {'kind':'openai','base_url':'http://localhost:10001/v1','model':'a','name':'First','priority':1,'_profile_id':'a'}
        self.b = dict(self.a,model='b',priority=2,_profile_id='b')

    def test_schedule_boundaries_midnight_and_overnight(self):
        p = dict(self.a,schedule_enabled=True,schedule_start='20:00',schedule_end='24:00')
        at = lambda h,m=0: dt.datetime(2026,10,26,h,m)
        for h,m,allowed in [(19,59,False),(20,0,True),(23,59,True),(0,0,False)]:
            self.assertEqual(route.in_schedule(p,at(h,m)), allowed)
        p.update(schedule_start='22:00',schedule_end='06:00')
        self.assertTrue(route.in_schedule(p,at(2)))
        self.assertFalse(route.in_schedule(p,at(6)))

    def test_priority_tie_active_and_exclusion(self):
        config = {'active':'b','profiles':{'a':self.a,'b':self.b}}
        self.assertEqual(route.candidates(config)[0]['_profile_id'], 'a')
        config['profiles']['b'] = dict(self.b,priority=1)
        self.assertEqual(route.candidates(config)[0]['_profile_id'], 'b')
        config['profiles']['b']['routing_enabled'] = False
        self.assertEqual(len(route.candidates(config)), 1)

    def test_role_filters_communication_and_coding_profiles(self):
        config = {'active':'chat','profiles':{
            'chat': dict(self.a, model='chat', priority=2, roles=['communication']),
            'code': dict(self.b, model='code', priority=1, roles=['coding', 'planning']),
        }}
        self.assertEqual(route.candidates(config, role='coding')[0]['_profile_id'], 'code')
        self.assertEqual(route.candidates(config, role='communication')[0]['_profile_id'], 'chat')
        self.assertEqual(route.candidates({'active':'legacy','profiles':{'legacy':{'model':'legacy','priority':1}}}, role='communication')[0]['_profile_id'], 'legacy')
        self.assertEqual(route.candidates({'profiles':{'legacy':{'model':'legacy','priority':1}}}, role='planning'), [])
        self.assertEqual(route.roles({'model':'legacy'}), {'communication'})

    def test_snapshot_can_report_selected_role(self):
        config = {'active':'chat','profiles':{
            'chat': dict(self.a, model='chat', priority=2, roles=['communication']),
            'plan': dict(self.b, model='plan', priority=1, roles=['planning']),
        }}
        self.assertEqual(route.snapshot(config, role='planning')['role'], 'planning')
        self.assertEqual(route.snapshot(config, role='planning')['next'], 'plan')

    def test_validate_invalid_hours_priority_timeout(self):
        for patch_value in ({'priority':0},{'priority':10},{'priority':1.5},{'schedule_enabled':True,'schedule_start':'24:00'}, {'schedule_enabled':True,'schedule_end':'25:00'}, {'timeout':1}):
            with self.subTest(patch=patch_value), self.assertRaises(ValueError):
                route.validate({'a':dict(self.a,**patch_value)})

    def test_codex_unknown_is_not_zero_and_reset_releases_block(self):
        subscriptions._codex_limits({'rateLimits':{'primary':{'usedPercent':100,'resetsAt':200}}})
        p = dict(self.a,kind='codex_subscription')
        self.assertTrue(route.blocked(p,now=100))
        self.assertFalse(route.blocked(p,now=201))
        subscriptions._codex_limits({'rateLimits':{'primary':{'usedPercent':None}}})
        self.assertFalse(route.blocked(p))

    def test_claude_fraction_and_unknown_usage(self):
        info = subscriptions._claude_limits({'status':'allowed','unifiedWindows':{'five_hour':{'utilization':.24,'resetsAt':200}}})
        self.assertEqual(info['buckets'][0]['windows'][0]['remaining_percent'],76)
        info = subscriptions._claude_limits({'status':'allowed'})
        self.assertIsNone(info['buckets'][0]['windows'][0]['remaining_percent'])

    def test_failover_and_cooldown(self):
        calls = []
        def stream(p, *args):
            calls.append(p['model'])
            if p['model'] == 'a': raise llm.LLMError('429 quota exhausted')
            yield 'text','OK'
        with patch('llm._stream_events_single', side_effect=stream):
            self.assertEqual(list(llm.stream_events(dict(self.a,_route_candidates=[self.a,self.b]),'s',[])), [('text','OK')])
            self.assertEqual(calls,['a','b'])
            self.assertTrue(route.blocked(self.a))
            self.assertEqual(route.snapshot({'profiles':{'a':self.a,'b':self.b}})['selected']['id'],'b')

    def test_never_replay_after_text_or_tools(self):
        for first in [('text','partial'),('tools',[{'name':'write_file'}])]:
            calls = []
            def stream(p,*args):
                calls.append(p['model']); yield first
                raise llm.LLMError('connection lost')
            route._health.clear()
            with patch('llm._stream_events_single', side_effect=stream):
                with self.assertRaises(llm.LLMError):
                    list(llm.stream_events(dict(self.a,_route_candidates=[self.a,self.b]),'s',[]))
            self.assertEqual(calls,['a'])

    def test_timeout_cooldown_recovers(self):
        with patch('modelrouter.time.time', return_value=100):
            route.failed(self.a, 'сервер модели не ответил вовремя')
        self.assertTrue(route.blocked(self.a,now=159))
        self.assertFalse(route.blocked(self.a,now=161))

    def test_background_quota_refresh_does_not_reset_backoff(self):
        route.failed(self.a, '429 quota exhausted')
        route.refresh({'profiles':{}})
        self.assertTrue(route.blocked(self.a))
        route.refresh({'profiles':{}}, retry=True)
        self.assertFalse(route.blocked(self.a))

    def test_cancel_does_not_try_another_model(self):
        cancel = threading.Event(); cancel.set()
        with patch('llm._stream_events_single') as stream:
            self.assertEqual(list(llm.stream_events(dict(self.a,_route_candidates=[self.a,self.b]),'s',[],cancel)), [])
            stream.assert_not_called()

    def test_egress_policy_excludes_external_candidates(self):
        egress.set_local_only(True)
        c = dict(self.b,base_url='https://api.openai.com/v1')
        self.assertEqual(len(route.candidates({'profiles':{'a':self.a,'b':c}})),1)

    def test_api_headers_are_not_a_monetary_balance(self):
        route.observe_headers(self.a,{'x-ratelimit-remaining-tokens':'1234'})
        value = route.snapshot({'profiles':{'a':self.a}})['profiles'][0]['quota']
        self.assertEqual(value['remaining']['tokens'],1234)
        self.assertNotIn('credit_balance',value)


if __name__ == '__main__': unittest.main()
