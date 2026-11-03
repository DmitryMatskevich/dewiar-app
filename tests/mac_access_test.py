"""Explicit core folder probes and native permission setup; no real protected OS prompts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'core'),str(ROOT/'ui')]
from mac_access import folder_access
from macshell import make_api,access_setup_on_start,_core_request
import app


class AccessTests(unittest.TestCase):
    def test_native_request_ignores_events_until_its_matching_reply(self):
        ws=Mock()
        result={'state':'authorized','readable':True,'path':'/fixture/Documents'}
        ws.recv.side_effect=[json.dumps(x) for x in [
            {'type':'level','level':0}, {'type':'hello','data':{}},
            {'type':'chat_changed'}, {'type':'reply','id':9,'data':None},
            {'type':'reply','id':1,'ok':True,'data':result}]]
        with patch('websockets.sync.client.connect') as connect:
            connect.return_value.__enter__.return_value=ws
            self.assertEqual(_core_request('fixture','mac_files_access',{'target':'documents'}),result)
        self.assertEqual(ws.recv.call_count,5)
        self.assertEqual(json.loads(ws.send.call_args.args[0])['args'],{'target':'documents'})

    def test_native_request_keeps_one_timeout_and_reports_reply_errors(self):
        ws=Mock()
        ws.recv.side_effect=[json.dumps({'type':'hello'}),json.dumps({'type':'level'})]
        with patch('websockets.sync.client.connect') as connect, patch('macshell.time.monotonic',side_effect=[0,1,2,4]):
            connect.return_value.__enter__.return_value=ws
            with self.assertRaises(TimeoutError):_core_request('fixture','mac_files_access',timeout=3)
        self.assertEqual([c.kwargs['timeout'] for c in ws.recv.call_args_list],[2,1])
        ws.recv.side_effect=[json.dumps({'type':'hello'}),json.dumps({'type':'reply','id':1,'ok':False,'error':'denied'})]
        with patch('websockets.sync.client.connect') as connect:
            connect.return_value.__enter__.return_value=ws
            with self.assertRaisesRegex(RuntimeError,'denied'):_core_request('fixture','mac_files_access')

    def test_status_only_does_not_touch_protected_folders(self):
        with patch('mac_access.os.scandir') as scan:
            values=folder_access(SimpleNamespace(root=Path('/fixture/workspace')))
        scan.assert_not_called()
        self.assertEqual(values['documents']['state'],'unchecked')
        self.assertEqual(values['workspace']['path'],'/fixture/workspace')

    def test_explicit_probe_reads_directory_without_changing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'keep.txt').write_text('private content')
            ws=SimpleNamespace(root=root)
            self.assertEqual(folder_access(ws,'workspace')['state'],'authorized')
            self.assertEqual(folder_access(ws,'selected',folder)['state'],'authorized')
            self.assertEqual(list(root.iterdir()),[root/'keep.txt'])
            self.assertEqual(ws.root,root)
            with patch('mac_access.os.scandir',side_effect=PermissionError()):
                self.assertEqual(folder_access(ws,'documents')['state'],'denied')
            self.assertEqual(folder_access(ws,'selected',str(root/'missing'))['state'],'missing')
            with self.assertRaises(ValueError):folder_access(ws,'arbitrary')
            with self.assertRaises(ValueError):folder_access(ws,'selected','relative')

    def test_startup_once_or_explicit_every_launch_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'settings.json'
            self.assertTrue(access_setup_on_start(file))
            file.write_text(json.dumps({'ui':{'mac_access_setup_seen':True}}))
            self.assertFalse(access_setup_on_start(file))
            file.write_text(json.dumps({'ui':{'mac_access_setup_seen':True,'mac_access_at_startup':True}}))
            self.assertTrue(access_setup_on_start(file))

    @unittest.skipUnless(sys.platform=='darwin','native wrappers require Cocoa')
    def test_screen_request_dispatched_once_and_uses_settings_after_denial(self):
        with patch('Quartz.CGPreflightScreenCaptureAccess',return_value=False),patch('PyObjCTools.AppHelper.callAfter') as dispatch,patch.object(sys,'frozen',True,create=True),patch('macsys.open_target') as opened:
            api=make_api(app)('fixture')
            with patch('macsys.request_screen_capture_permission',return_value=False) as request:
                self.assertTrue(api.request_screen_access()['pending'])
                api.request_screen_access();dispatch.assert_called_once();request.assert_not_called()
                dispatch.call_args.args[0]()
                api.request_screen_access();request.assert_called_once()
            self.assertFalse(api.screen_capture_permission()['pending'])
            self.assertIn('Privacy_ScreenCapture',opened.call_args.args[0])

    @unittest.skipUnless(sys.platform=='darwin','native wrappers require Cocoa')
    def test_existing_screen_permission_does_not_prompt(self):
        with patch('Quartz.CGPreflightScreenCaptureAccess',return_value=True),patch('PyObjCTools.AppHelper.callAfter') as dispatch,patch.object(sys,'frozen',True,create=True):
            self.assertTrue(make_api(app)('fixture').request_screen_access()['granted'])
            dispatch.assert_not_called()

    @unittest.skipUnless(sys.platform=='darwin','native wrappers require Cocoa')
    def test_source_cannot_request_permissions_for_wrong_app(self):
        with patch('Quartz.CGPreflightScreenCaptureAccess',return_value=False),patch('PyObjCTools.AppHelper.callAfter') as dispatch,patch.object(sys,'frozen',False,create=True),patch('macshell._core_request',return_value={}) as core:
            api=make_api(app)('fixture')
            self.assertFalse(api.request_screen_access()['request_supported'])
            self.assertFalse(api.request_files_access()['request_supported'])
            dispatch.assert_not_called()
            self.assertTrue(all(call.args[2:] == () for call in core.call_args_list))

    def test_files_requested_only_by_click_and_not_repeated_while_pending(self):
        folders={'workspace':{'path':'/fixture/workspace','state':'unchecked'}}
        def core(token,cmd,args=None,**kwargs):
            if not args:return dict(folders)
            return {'path':'/fixture/workspace','state':'authorized','readable':True}
        with patch('macshell._core_request',side_effect=core) as request,patch.object(sys,'frozen',True,create=True),patch('macshell.threading.Thread') as thread:
            api=make_api(app)('fixture');api.files_access_state()
            self.assertTrue(all(len(call.args)<3 for call in request.call_args_list))
            self.assertEqual(api.request_files_access('workspace')['pending'],'workspace')
            api.request_files_access('documents');thread.assert_called_once()
            thread.call_args.kwargs['target']()
            self.assertEqual(api.files_access_state()['folders']['workspace']['state'],'authorized')
            self.assertFalse(api.files_access_state()['pending'])

    def test_selected_folder_cancel_does_not_probe_or_change_workspace(self):
        with patch('macshell._core_request',return_value={}) as core,patch.object(sys,'frozen',True,create=True),patch('macshell.threading.Thread') as thread:
            api=make_api(app)('fixture');api.pick_folder=Mock(return_value='')
            api.request_files_access('selected');thread.call_args.kwargs['target']()
            self.assertTrue(all(len(call.args)<3 for call in core.call_args_list))
            self.assertFalse(api.files_access_state()['pending'])

    def test_denied_folder_opens_correct_settings(self):
        def core(token,cmd,args=None,**kwargs):
            return {'state':'denied','path':'/fixture/Documents','readable':False} if args else {'documents':{'state':'unchecked','path':'/fixture/Documents'}}
        with patch('macshell._core_request',side_effect=core),patch.object(sys,'frozen',True,create=True),patch('macshell.threading.Thread') as thread:
            api=make_api(app)('fixture');api.open_files_settings=Mock()
            api.request_files_access('documents');thread.call_args.kwargs['target']()
            api.open_files_settings.assert_called_once()
            self.assertEqual(api.files_access_state()['folders']['documents']['state'],'denied')

    def test_actual_core_dispatch_exposes_status_without_probe(self):
        from engine_commands import CommandsMixin
        engine=SimpleNamespace(workspace=SimpleNamespace(root=Path('/fixture/workspace')))
        with patch('mac_access.os.scandir') as scan:
            value=CommandsMixin.command(engine,'mac_files_access',{})
        self.assertEqual(value['workspace']['state'],'unchecked')
        scan.assert_not_called()

    def test_feature_access_rechecks_both_folders_and_discards_old_grants(self):
        folders={key:{'path':'/fixture/'+key,'state':'unchecked'} for key in ('documents','workspace')}
        denied=False
        def core(token,cmd,args=None,**kwargs):
            if not args:return {key:dict(value) for key,value in folders.items()}
            return dict(folders[args['target']], state='denied' if denied else 'authorized', readable=not denied)
        with patch('macshell._core_request',side_effect=core) as request, patch.object(sys,'frozen',True,create=True), patch('macshell.threading.Thread') as thread, patch('mac_screen_access.mark_requested') as marked, patch('mac_screen_access.was_requested',return_value=True):
            api=make_api(app)('fixture');api.open_files_settings=Mock()
            api.request_files_access('features');thread.call_args.kwargs['target']()
            self.assertEqual([c.args[2]['target'] for c in request.call_args_list if len(c.args)>2],['documents','workspace'])
            self.assertTrue(all(v['readable'] for v in api.files_access_state()['folders'].values()))
            marked.assert_called_once_with('files')
            denied=True
            self.assertEqual(api.files_access_state(refresh=True)['pending'],'features')
            self.assertTrue(all(v['state']=='unchecked' for v in api.files_access_state()['folders'].values()))
            thread.call_args.kwargs['target']()
            self.assertTrue(all(v['state']=='denied' for v in api.files_access_state()['folders'].values()))
            api.open_files_settings.assert_not_called()  # Focus refresh does not open Settings.
            api.request_files_access('features');thread.call_args.kwargs['target']()
            api.open_files_settings.assert_called_once()  # Explicit click offers recovery.

    def test_focus_before_any_request_does_not_prompt_for_files(self):
        with patch('macshell._core_request',return_value={}), patch.object(sys,'frozen',True,create=True), patch('mac_screen_access.was_requested',return_value=False), patch('macshell.threading.Thread') as thread:
            make_api(app)('fixture').files_access_state(refresh=True)
            thread.assert_not_called()


if __name__=='__main__':unittest.main()
