"""Private IPC tests: only temporary helper scripts; no network or settings."""
import concurrent.futures
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from content.mcp_helper import call, HelperError


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.entry = Path(self.tmp.name) / 'helper.py'
    def script(self, tail):
        self.entry.write_text("import json,sys,time,os\nf=json.loads(sys.stdin.buffer.readline())\n" + tail)
    def call(self, **kwargs): return call(sys.executable, 'health', _entry=self.entry, **kwargs)
    def reply(self, extra=''):
        return extra + "\nprint(json.dumps({'v':1,'id':f['id'],'ok':True,'result':{'state':'ready','sdk':'2.3.0','protocol':1,'can_generate':False}}))\n"

    def test_isolated_argv_environment_and_success(self):
        os.environ['DEWIAR_TEST_PRIVATE_SENTINEL'] = 'never-forwarded'
        self.addCleanup(os.environ.pop, 'DEWIAR_TEST_PRIVATE_SENTINEL', None)
        self.script(self.reply("assert sys.flags.isolated and sys.dont_write_bytecode\nassert 'DEWIAR_TEST_PRIVATE_SENTINEL' not in os.environ\nassert not any(p.endswith('/core') for p in sys.path)"))
        self.assertEqual(self.call()['state'], 'ready')

    def test_timeout_terminates_only_child(self):
        marker = Path(self.tmp.name) / 'late'
        self.script('time.sleep(1)\nopen('+repr(str(marker))+',"w").write("late")')
        started = time.monotonic()
        with self.assertRaisesRegex(HelperError, 'timeout'): self.call(timeout=.12)
        self.assertLess(time.monotonic()-started, 1)
        self.assertFalse(marker.exists())

    def test_cancel_interrupts_blocked_read(self):
        self.script('time.sleep(10)')
        event = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.call, cancel=event)
            event.set()
            with self.assertRaisesRegex(HelperError, 'cancelled'): future.result(timeout=2)

    def test_oversized_and_multiple_frames_fail_closed(self):
        for tail in ["print('x'*300000)", self.reply()+"print('unexpected')", "print('x')", "sys.exit(3)"]:
            self.script(tail)
            with self.assertRaisesRegex(HelperError, 'protocol_error'): self.call()

    def test_wrong_id_unknown_fields_and_untrusted_error_are_not_exposed(self):
        frames = ["{'v':1,'id':'wrong','ok':False,'error':'operation_failed'}",
                  "{'v':1,'id':f['id'],'ok':False,'error':'SECRET-OAUTH-BODY'}",
                  "{'v':True,'id':f['id'],'ok':False,'error':'operation_failed'}",
                  "{'v':1,'id':f['id'],'ok':True,'result':{'can_generate':True,'access_token':'SECRET'}}"]
        for frame in frames:
            self.script('print(json.dumps('+frame+'))')
            with self.assertRaisesRegex(HelperError, '^protocol_error$'): self.call()

    def test_stderr_is_drained_without_publication(self):
        self.script(self.reply("sys.stderr.write('SECRET'*100000);sys.stderr.flush()"))
        self.assertNotIn('SECRET', repr(self.call()))

    def test_known_error_and_missing_runtime(self):
        self.script("print(json.dumps({'v':1,'id':f['id'],'ok':False,'error':'runtime_unavailable'}))")
        with self.assertRaisesRegex(HelperError, '^runtime_unavailable$'): self.call()
        with self.assertRaisesRegex(HelperError, '^runtime_unavailable$'): call('/nonexistent/python', 'health')
        with self.assertRaisesRegex(HelperError, '^unsupported_operation$'): call(sys.executable, 'tools/call')

    def test_duplicate_json_keys_and_extra_result_fields(self):
        self.script("print('{\"v\":1,\"v\":1,\"id\":\"'+f['id']+'\",\"ok\":false,\"error\":\"operation_failed\"}')")
        with self.assertRaisesRegex(HelperError, '^protocol_error$'): self.call()
        self.script(self.reply().replace("'state':'ready'", "'token':'SECRET','state':'ready'"))
        with self.assertRaisesRegex(HelperError, '^protocol_error$'): self.call()

    def test_login_event_then_cancel_and_foreign_url(self):
        from urllib.parse import urlencode
        params={'response_type':'code','client_id':'FAKE','resource':'https://mcp.higgsfield.ai/mcp','redirect_uri':'http://127.0.0.1:12345/oauth/'+'a'*32,'state':'b'*43,'code_challenge':'c'*43,'code_challenge_method':'S256'}
        url='https://clerk.higgsfield.ai/oauth/authorize?'+urlencode(params)
        self.script("print(json.dumps({'v':1,'id':f['id'],'event':'authorization_url','url':"+repr(url)+"}),flush=True)\ntime.sleep(10)")
        cancel=threading.Event();seen=[]
        def on_url(value):seen.append(value);cancel.set()
        with self.assertRaisesRegex(HelperError,'cancelled'):call(sys.executable,'authorize',_entry=self.entry,on_authorization_url=on_url,cancel=cancel,timeout=2)
        self.assertEqual(len(seen),1)

        self.script("print(json.dumps({'v':1,'id':f['id'],'event':'authorization_url','url':'https://wrong.example/authorize'}))")
        with self.assertRaisesRegex(HelperError,'protocol_error'):call(sys.executable,'authorize',_entry=self.entry,on_authorization_url=seen.append,timeout=2)
        self.assertEqual(len(seen),1)

    def test_large_private_input_cannot_block_timeout(self):
        from content_oauth_store_unittest import result
        value=result().credential;value.value['oauth_metadata']['test_padding']='x'*180000
        self.entry.write_text('import time\ntime.sleep(10)')
        started=time.monotonic()
        with self.assertRaisesRegex(HelperError,'timeout'):call(sys.executable,'refresh',credential=value,_entry=self.entry,timeout=.15)
        self.assertLess(time.monotonic()-started,1)


@unittest.skipUnless(os.environ.get('CONTENT_MCP_TEST_PYTHON'),'separate optional SDK runtime required')
class SDKProcessTests(unittest.TestCase):
    def test_actual_health_process_uses_optional_sdk(self):
        result=call(os.environ['CONTENT_MCP_TEST_PYTHON'],'health')
        self.assertEqual(result['sdk'],'2.3.0')

    def test_full_fake_login_over_private_ipc(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'entry.py'
            script.write_text('import sys,asyncio,logging\n'
                +'sys.path.insert(0,'+repr(str(root/'tools/content_mcp_runtime'))+')\n'
                +'sys.path.insert(0,'+repr(str(root/'tests'))+')\n'
                +'import main,oauth,httpx2\nfrom content_mcp_callback_unittest import LoginTests\n'
                +'fixture=LoginTests();fixture.setUp();original=oauth.authorize\n'
                +'async def fake(on_url):\n'
                +' async def callback(url):\n  await on_url(url)\n  await fixture.on_url(url)\n'
                +' return await original(callback,httpx2.MockTransport(fixture.route))\n'
                +'oauth.authorize=fake\nmain.main()\n')
            events=[]
            result=call(os.environ['CONTENT_MCP_TEST_PYTHON'],'authorize',_entry=script,on_authorization_url=events.append,timeout=5)
            self.assertEqual(len(events),1);self.assertEqual(result.report['state'],'tools_discovered')
            self.assertEqual(result.credential.value['tokens']['access_token'],'FAKE-access')
            self.assertNotIn('FAKE-access',repr(result));self.assertNotIn('FAKE-refresh',repr(result.credential))


if __name__ == '__main__': unittest.main()
