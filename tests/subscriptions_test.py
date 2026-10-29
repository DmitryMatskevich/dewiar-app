"""Subscription transport contract, privacy gates and guarded text tools; no paid network calls."""
import sys
import unittest
import threading
import time
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
import subscriptions as sub
import llm
import egress
import security


class FakeSession:
    provider = ''
    events = []
    auth = True
    instances = []

    def __init__(self, provider, extra=()):
        self.provider = provider
        self.extra = extra
        self.tmp = type('Temp', (), {'name': '/tmp/dewiar-empty'})()
        self.sent = []
        self.closed = False
        self.events = list(type(self).events)
        self.instances.append(self)

    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def initialize(self):
        return {'account': {'subscriptionType': 'Claude Max' if self.auth else 'API', 'email': 'private@example.org'},
                'models': [{'value': 'haiku', 'displayName': 'Haiku'}]}
    def rpc(self, method, params=None, timeout=25):
        self.sent.append((method, params))
        if method == 'account/read': return {'account': {'type': 'chatgpt' if self.auth else 'apiKey', 'planType': 'pro', 'email': 'private@example.org'}}
        if method == 'model/list': return {'data': [{'id': 'test-model', 'displayName': 'Test', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]}
        if method == 'thread/start': return {'thread': {'id': 'owned-thread'}}
        return {}
    def send(self, obj): self.sent.append(obj)
    def event(self, deadline, cancel=None):
        if cancel and cancel.is_set(): raise InterruptedError()
        if not self.events: raise TimeoutError('fixture exhausted')
        return self.events.pop(0)


class Subscriptions(unittest.TestCase):
    def setUp(self):
        egress.set_local_only(False)
        FakeSession.auth = True
        FakeSession.events = []
        FakeSession.instances.clear()
    def tearDown(self): egress.set_local_only(False)

    def test_local_privacy_blocks_before_process_spawn(self):
        egress.set_local_only(True)
        with patch('subscriptions.subprocess.Popen') as spawn:
            for provider in ('codex', 'claude'):
                with self.assertRaisesRegex(RuntimeError, 'только локально'):
                    sub.Session(provider)
            with self.assertRaisesRegex(RuntimeError, 'только локально'):
                sub.login('claude')
            spawn.assert_not_called()

    def test_subscription_is_never_local_even_if_profile_url_is_local(self):
        s = type('Settings', (), {'get': lambda self, *args: {'sub': {'kind': 'codex_subscription', 'base_url': 'http://localhost:9999'}, 'local': {'kind': 'ollama', 'base_url': 'http://localhost:11434'}}})()
        self.assertEqual([k for k, _ in security.local_profiles(s)], ['local'])

    def test_environment_excludes_api_credentials_and_provider_overrides(self):
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'secret', 'OPENAI_API_KEY': 'secret',
                                       'ANTHROPIC_BASE_URL': 'https://other', 'CLAUDE_CODE_USE_BEDROCK': '1',
                                       'CLAUDE_CODE_OAUTH_TOKEN': 'secret', 'CLAUDECODE': '1'}):
            env = sub._env('claude')
        self.assertFalse(any(k.startswith(('ANTHROPIC_', 'OPENAI_', 'CLAUDE_CODE_')) for k in env))
        self.assertNotIn('CLAUDECODE', env)

    @patch('subscriptions.executable', return_value='/official/cli')
    @patch('subscriptions.Session', FakeSession)
    def test_status_is_metadata_only_and_redacts_account(self, _):
        for provider in ('codex', 'claude'):
            result = sub.status(provider)
            self.assertTrue(result['authenticated'])
            self.assertTrue(result['models'])
            self.assertNotIn('email', str(result))
            self.assertNotIn('private@example', str(result))
            self.assertTrue(FakeSession.instances[-1].closed)
            self.assertFalse(any(isinstance(x, tuple) and x[0] == 'turn/start' for x in FakeSession.instances[-1].sent))

    @patch('subscriptions.Session', FakeSession)
    def test_limits_read_does_not_require_workspace_discovery_or_generation(self):
        sub.read_limits('codex')
        sent = FakeSession.instances[-1].sent
        self.assertTrue(any(x[0] == 'account/rateLimits/read' for x in sent))
        self.assertFalse(any(x[0] in ('account/read','model/list','turn/start') for x in sent))

    @patch('subscriptions.Session', FakeSession)
    def test_codex_stream_uses_restricted_ephemeral_thread(self):
        FakeSession.events = [{'method': 'item/agentMessage/delta', 'params': {'delta': 'Готово'}},
                              {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}]
        result = list(sub.stream({'kind': 'codex_subscription', 'model': 'test-model', 'reasoning_effort': 'high'}, 'system', [{'role': 'user', 'content': 'hi'}]))
        self.assertEqual(result, ['Готово'])
        session = FakeSession.instances[-1]
        thread = dict(session.sent)['thread/start']
        turn = dict(session.sent)['turn/start']
        self.assertTrue(thread['ephemeral'])
        self.assertEqual(thread['approvalPolicy'], 'never')
        self.assertEqual(turn['effort'], 'high')
        self.assertEqual(thread['permissions'], 'dewiar_text')
        self.assertEqual(turn['permissions'], 'dewiar_text')
        self.assertNotIn('sandbox', thread)
        self.assertNotIn('sandboxPolicy', turn)
        self.assertTrue(session.closed)

    @patch('subscriptions.Session', FakeSession)
    def test_claude_stream_does_not_duplicate_final_text(self):
        FakeSession.events = [{'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'OK'}}},
                              {'type': 'result', 'is_error': False, 'result': 'OK'}]
        self.assertEqual(list(sub.stream({'kind': 'claude_subscription', 'model': 'haiku'}, 'sys', [])), ['OK'])
        self.assertIn('--system-prompt', FakeSession.instances[-1].extra)

    @patch('subscriptions.Session', FakeSession)
    def test_auth_required_without_api_fallback(self):
        FakeSession.auth = False
        for provider in ('codex', 'claude'):
            with self.assertRaises(RuntimeError):
                list(sub.stream({'kind': provider + '_subscription', 'model': 'test'}, 'sys', []))
            self.assertTrue(FakeSession.instances[-1].closed)

    @patch('subscriptions.Session', FakeSession)
    def test_auth_and_quota_errors_do_not_become_successful_answers(self):
        FakeSession.events = [{'type': 'result', 'is_error': True, 'result': 'OAuth access token is invalid'}]
        result = llm.test_profile({'kind': 'claude_subscription', 'model': 'haiku'})
        self.assertFalse(result['ok'])
        self.assertIn('Вход Claude Code', result['error'])

    @patch('subscriptions.Session', FakeSession)
    def test_cancellation_closes_owned_runtime(self):
        cancel = threading.Event(); cancel.set()
        self.assertEqual(list(llm.stream_events({'kind': 'claude_subscription', 'model': 'haiku'}, '', [], cancel)), [])
        self.assertTrue(FakeSession.instances[-1].closed)

    def test_images_are_rejected_before_runtime_spawn(self):
        with patch('subscriptions.Session') as session:
            with self.assertRaisesRegex(RuntimeError, 'только текст'):
                list(sub.stream({'kind': 'codex_subscription', 'model': 'test'}, '', [{'role': 'user', 'content': [{'type': 'image_url'}]}]))
            session.assert_not_called()

    def test_text_tool_calls_go_through_dewiar_and_filter_unknown_names(self):
        chunks = ['Думаю. <tool_', 'call>{"name":"get_time","arguments":{}}</tool_call>', 'Фиктивный результат']
        with patch('subscriptions.stream', return_value=iter(chunks)):
            events = list(llm.stream_events({'kind': 'codex_subscription'}, '', [], tools=[llm.PROBE_TOOL]))
        self.assertEqual(''.join(v for k,v in events if k == 'text'), 'Думаю. ')
        self.assertEqual(events[-1][0], 'tools')
        self.assertEqual(events[-1][1][0]['name'], 'get_time')
        with patch('subscriptions.stream', return_value=iter(['<tool_call>{"name":"unknown","arguments":{}}</tool_call>'])):
            self.assertFalse(any(k == 'tools' for k,v in llm.stream_events({'kind': 'codex_subscription'}, '', [], tools=[llm.PROBE_TOOL])))

    def test_startup_tools_probe_never_spends_subscription(self):
        with patch('subscriptions.Session') as session:
            self.assertEqual(llm.probe_tools({'kind': 'codex_subscription'}), 'prompt')
            self.assertEqual(llm.probe_tools({'kind': 'claude_subscription'}), 'prompt')
            session.assert_not_called()

    def test_login_completion_requires_success_and_expected_attempt(self):
        with FakeSession('codex') as session:
            session.events = [{'method':'account/login/completed','params':{'loginId':'other','success':True}},
                              {'method':'account/login/completed','params':{'loginId':'mine','success':True}}]
            self.assertEqual(sub._await_codex_login(session,'mine'), {'success':True})
            session.events = [{'method':'account/login/completed','params':{'loginId':'mine','success':False}}]
            with self.assertRaisesRegex(RuntimeError,'отклонил'):
                sub._await_codex_login(session,'mine')

    def test_claude_launch_disables_customizations_but_keeps_subscription_mode(self):
        from unittest.mock import MagicMock
        proc = MagicMock();proc.poll.return_value=0;proc.stdout.__iter__.return_value=iter([])
        with patch('subscriptions.executable',return_value='/official/claude'), patch('subscriptions.subprocess.Popen',return_value=proc) as spawn:
            with sub.Session('claude'):
                cmd=spawn.call_args.args[0]
                self.assertIn('--safe-mode',cmd)
                self.assertNotIn('--bare',cmd)  # bare mode disables subscription credentials
                self.assertEqual(cmd[cmd.index('--tools')+1],'')
                self.assertIn('--strict-mcp-config',cmd)

    def test_runtime_requests_for_tools_are_rejected(self):
        session = object.__new__(sub.Session)
        session.pending = []; session.q = sub.queue.Queue(); session.send = lambda obj: None
        session.q.put({'id': 99, 'method': 'item/tool/call'})
        with self.assertRaisesRegex(RuntimeError, 'действие'):
            session.event(time.monotonic() + 1)

    def test_rpc_preserves_early_stream_deltas(self):
        session = object.__new__(sub.Session)
        session.pending = []; session.seq = 0; session.q = sub.queue.Queue(); session.send = lambda obj: None
        delta = {'method': 'item/agentMessage/delta', 'params': {'delta': 'early'}}
        session.q.put(delta); session.q.put({'id': 1, 'result': {'ok': True}})
        self.assertEqual(session.rpc('turn/start'), {'ok': True})
        self.assertEqual(session.event(time.monotonic()+1), delta)


class ProcessTransport(unittest.TestCase):
    def test_real_process_jsonl_contract_and_cleanup(self):
        # A real subprocess catches framing, pipe buffering and lifecycle bugs.
        script = r'''#!/usr/bin/env python3
import json,sys
for line in sys.stdin:
 r=json.loads(line); method=r.get("method")
 def emit(o): print(json.dumps(o),flush=True)
 if method=="initialize":emit({"id":r["id"],"result":{}})
 elif method=="account/read":emit({"id":r["id"],"result":{"account":{"type":"chatgpt"}}})
 elif method=="thread/start":emit({"id":r["id"],"result":{"thread":{"id":"test"}}})
 elif method=="turn/start":
  emit({"id":r["id"],"result":{}})
  emit({"method":"item/agentMessage/delta","params":{"delta":"Готово"}})
  emit({"method":"turn/completed","params":{"turn":{"status":"completed"}}})
 elif r.get("type")=="control_request":emit({"type":"control_response","response":{"subtype":"success","response":{"account":{"subscriptionType":"Claude Max"}}}})
 elif r.get("type")=="user":emit({"type":"result","is_error":False,"result":"Готово"})
'''
        egress.set_local_only(False)
        with tempfile.TemporaryDirectory() as tmp:
            cli=Path(tmp)/'official-fixture'; cli.write_text(script); cli.chmod(0o700)
            # Use this venv's Python for the fixture, including GUI-like minimal PATH.
            script=script.replace('#!/usr/bin/env python3', '#!'+sys.executable)
            cli.write_text(script)
            with patch('subscriptions.executable', return_value=str(cli)):
                for provider in ('codex','claude'):
                    self.assertEqual(list(sub.stream({'kind':provider+'_subscription','model':'fixture'},'sys',[])), ['Готово'])



if __name__ == '__main__': unittest.main()
