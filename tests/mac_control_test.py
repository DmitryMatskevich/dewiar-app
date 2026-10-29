"""Desktop control: isolated applications/windows; never changes the user's desktop."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
import mac_control as mc
import tools
from engine_music import MusicMixin


def display(number,x,y=25,w=1440,h=875):
    return dict(id=number,number=number,x=x,y=y,w=w,h=h,full=(x,y,w,h))


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.app=Mock();self.app.terminate.return_value=True;self.app.isTerminated.return_value=True
        self.app.activateWithOptions_.return_value=True
        self.row=dict(name='Safari',id='com.apple.Safari',pid=123,_app=self.app)
        self.screen=display(1,0)
        self.rect=dict(x=100,y=125,w=800,h=600)

    def test_russian_commands(self):
        for text,action in [('Открой Калькулятор','open'),('Закрой приложение Safari','quit'),('Переключись на Safari','focus'),('Перенеси Safari на второй экран','move'),('Перемести курсор мыши на Safari','cursor'),('Покажи мониторы','screens')]:
            self.assertEqual(mc.parse(text)['action'],action)
        for text in ['открой сайт пример','закрой задачу','запусти файл','как закрыть Safari?']:
            self.assertIsNone(mc.parse(text))

    def test_resolve_never_fuzzy_or_substring_inside_word(self):
        rows=[dict(name='Xcode',id='com.apple.dt.Xcode'),dict(name='Code',id='com.microsoft.VSCode')]
        self.assertEqual(mc.resolve('вскод',rows)['name'],'Code')
        with self.assertRaises(mc.AppNotFound):mc.resolve('code',rows[:1])
        with self.assertRaises(mc.AppNotFound):mc.resolve('Safarii',[self.row])
        with self.assertRaisesRegex(ValueError,'Уточните'):mc.resolve('Safari',[self.row,self.row])

    def test_screens_negative_origins_and_small_target(self):
        left=display(2,-1280,-500,1280,720)
        source,target=mc.choose_screen('левый',[self.screen,left],self.rect)
        fitted=mc.fit(dict(x=0,y=25,w=1600,h=1000),source,target)
        self.assertEqual(fitted,dict(x=-1280,y=-500,w=1280,h=720))
        self.assertEqual(mc.choose_screen('2',[self.screen,left],self.rect)[1],left)

    def test_screen_errors(self):
        for name in ['другой','2','левый']:
            with self.assertRaises(ValueError):mc.choose_screen(name,[self.screen],self.rect)
        with self.assertRaisesRegex(ValueError,'дублируются'):
            mc.choose_screen('другой',[self.screen,dict(self.screen,id=2,number=2)],self.rect)

    def test_open_uses_fixed_argv(self):
        with patch.object(mc,'running',return_value=[dict(id='test')]) as running,patch.object(mc,'applications',return_value=[dict(name='Test',id='test',path='/Applications/Test $(x).app')]),patch.object(mc.subprocess,'run') as run:
            run.return_value.returncode=0
            mc.execute(dict(action='open',application='Test'))
            self.assertEqual(run.call_args.args[0],['/usr/bin/open','-a','/Applications/Test $(x).app'])
            self.assertNotIn('shell',run.call_args.kwargs)
            running.assert_not_called()

    def test_quit_never_forces_and_preserves_save_dialog(self):
        self.app.isTerminated.return_value=False
        with patch.object(mc,'running',return_value=[self.row]),patch.object(mc.time,'sleep'):
            result=mc.execute(dict(action='quit',application='Safari'))
        self.assertIn('запрошено',result['message']);self.app.terminate.assert_called_once()
        self.app.forceTerminate.assert_not_called()

    def test_finder_and_self_are_not_terminated(self):
        for appid in ['com.apple.finder','pro.dewiar.macos']:
            with patch.object(mc,'running',return_value=[dict(self.row,id=appid)]):
                with self.assertRaises(ValueError):mc.execute(dict(action='quit',application='Safari'))
        self.app.terminate.assert_not_called()

    def test_focus_no_accessibility_required(self):
        with patch.object(mc,'running',return_value=[self.row]),patch.object(mc,'permission') as perm:
            mc.execute(dict(action='focus',application='Safari'))
        perm.assert_not_called();self.app.activateWithOptions_.assert_called_once()

    def test_denied_permission_no_window_mutation(self):
        with patch.object(mc,'running',return_value=[self.row]),patch.object(mc,'permission',return_value={'granted':False}),patch.object(mc,'windows') as windows:
            with self.assertRaises(PermissionError):mc.execute(dict(action='move',application='Safari'))
            windows.assert_not_called()

    def invoke_window(self,action,**kwargs):
        import contextlib
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(mc,'running',return_value=[self.row]))
            stack.enter_context(patch.object(mc,'permission',return_value={'granted':True}))
            stack.enter_context(patch.object(mc,'windows',return_value=('root',[dict(number=1,title='test',rect=self.rect,_element='window')])))
            stack.enter_context(patch.object(mc,'screens',return_value=[self.screen,display(2,1440)]))
            stack.enter_context(patch.object(mc,'_get',side_effect=lambda e,n: 'window' if n=='AXFocusedWindow' else False))
            return mc.execute(dict(action=action,application='Safari',**kwargs))

    def test_window_move_verified_in_target(self):
        with patch.object(mc,'_set') as setval,patch.object(mc,'_rect',return_value=dict(self.rect,x=1540)):
            result=self.invoke_window('move',screen='2')
        self.assertEqual(result['screen'],2);self.assertEqual(setval.call_args.args[1],'AXPosition')
        with patch.object(mc,'_set'),patch.object(mc,'_rect',return_value=self.rect):
            with self.assertRaisesRegex(ValueError,'не подтвердило'):self.invoke_window('move',screen='2')

    def test_cursor_only_warp_no_click(self):
        with patch('ApplicationServices.AXUIElementPerformAction',return_value=0),patch('Quartz.CGWarpMouseCursorPosition',return_value=0) as warp,patch.object(mc,'_rect',return_value=self.rect),patch('Quartz.CGEventPost') as click:
            result=self.invoke_window('cursor')
        warp.assert_called_once();click.assert_not_called()
        self.assertEqual(result['point'],dict(x=500.,y=425.))

    def test_fullscreen_and_ambiguous_windows_rejected(self):
        with patch.object(mc,'running',return_value=[self.row]),patch.object(mc,'permission',return_value={'granted':True}),patch.object(mc,'windows',return_value=('root',[dict(_element='w',rect=self.rect)])),patch.object(mc,'_get',return_value=True):
            with self.assertRaisesRegex(ValueError,'полноэкранного'):mc.execute(dict(action='move',application='Safari'))
        with patch.object(mc,'running',return_value=[self.row]),patch.object(mc,'permission',return_value={'granted':True}),patch.object(mc,'windows',return_value=('root',[dict(_element='a'),dict(_element='b')])),patch.object(mc,'_get',return_value=None):
            with self.assertRaisesRegex(ValueError,'Несколько окон'):mc.execute(dict(action='cursor',application='Safari'))

    def test_dry_exam_and_server_policy(self):
        settings=Mock();settings.get.side_effect=lambda *a:{}
        box=tools.Toolbox(settings,None);box.hooks['server']=True
        with patch.dict(os.environ,{'DEWIAR_MAC_DESKTOP':''}),patch.object(tools,'DRY',None),patch.object(tools,'EXAM_LAUNCH',None):
            self.assertFalse(box.policy('control_mac_app')[0])
            with patch.dict(os.environ,{'DEWIAR_MAC_DESKTOP':'1'}):
                self.assertTrue(box.policy('control_mac_app')[0]);self.assertFalse(box.policy('open_app')[0])
        with tempfile.NamedTemporaryFile() as f,patch.object(tools,'EXAM_LAUNCH',f.name),patch.object(mc,'execute') as execute:
            tools.t_control_mac_app(dict(action='quit',application='Safari'),{})
            execute.assert_not_called();self.assertEqual(json.loads(Path(f.name).read_text())['tool'],'control_mac_app')

    def test_desktop_task_actions_are_in_tool_catalog(self):
        actions = next(x for x in tools.TOOLS['control_mac_app'][1]['properties']['action']['enum'] if x == 'inspect')
        self.assertEqual(actions, 'inspect')
        self.assertIn('click', tools.TOOLS['control_mac_app'][1]['properties']['action']['enum'])
        self.assertIn('type', tools.TOOLS['control_mac_app'][1]['properties']['action']['enum'])
        self.assertIn('key', tools.TOOLS['control_mac_app'][1]['properties']['action']['enum'])

    def test_type_action_sets_accessibility_value_after_inspect(self):
        target = object(); window = object()
        with patch.object(mc, 'running', return_value=[self.row]), \
             patch.object(mc, 'permission', return_value={'granted': True}), \
             patch.object(mc, '_window_item', return_value=('root', {'number': 1, '_element': window})), \
             patch.object(mc, '_find_ui', return_value=(target, {'role': 'AXTextField', 'settable': True}, {})), \
             patch.object(mc, '_activate_window'), patch.object(mc, '_set') as setter:
            result = mc.execute({'action': 'type', 'application': 'Safari', 'target': 'Поиск', 'text': 'Dewiar'})
        self.assertEqual(result['characters'], 6)
        setter.assert_called_once_with(target, 'AXValue', 'Dewiar')

    def test_click_action_performs_accessibility_press(self):
        target = object(); window = object()
        with patch.object(mc, 'running', return_value=[self.row]), \
             patch.object(mc, 'permission', return_value={'granted': True}), \
             patch.object(mc, '_window_item', return_value=('root', {'number': 1, '_element': window})), \
             patch.object(mc, '_find_ui', return_value=(target, {'role': 'AXButton'}, {})), \
             patch.object(mc, '_activate_window'), \
             patch('ApplicationServices.AXUIElementPerformAction', return_value=0) as press:
            result = mc.execute({'action': 'click', 'application': 'Safari', 'target': 'Сохранить'})
        self.assertIn('Нажат', result['message'])
        press.assert_called_once_with(target, 'AXPress')

    def test_key_action_is_sent_to_active_window(self):
        window = object()
        with patch.object(mc, 'running', return_value=[self.row]), \
             patch.object(mc, 'permission', return_value={'granted': True}), \
             patch.object(mc, '_window_item', return_value=('root', {'number': 1, '_element': window})), \
             patch.object(mc, '_activate_window'), patch.object(mc, '_key_event', return_value='enter') as key:
            result = mc.execute({'action': 'key', 'application': 'Safari', 'key': 'Enter'})
        self.assertIn('Клавиша', result['message'])
        key.assert_called_once_with('Enter')

    def test_key_event_supports_command_shortcut(self):
        with patch('Quartz.CGEventCreateKeyboardEvent', side_effect=['down', 'up']) as create, \
             patch('Quartz.CGEventSetFlags') as flags, patch('Quartz.CGEventPost') as post:
            self.assertEqual(mc._key_event('Cmd+A'), 'a')
        self.assertEqual(create.call_args_list[0].args[1:], (0, True))
        self.assertEqual(flags.call_count, 2); self.assertEqual(post.call_count, 2)

    def test_fast_path_respects_confirmation_and_disabled_tool(self):
        engine=SimpleNamespace(toolbox=Mock(),_say_quick=Mock())
        for policy in [(False,False),(True,True)]:
            engine.toolbox.policy.return_value=policy
            with patch.object(tools,'t_control_mac_app') as run:
                self.assertFalse(MusicMixin._pc_cmd(engine,'Открой Safari'));run.assert_not_called()
        engine.toolbox.policy.return_value=(True,False)
        with patch.object(tools,'t_control_mac_app',return_value='Запуск запрошен'):
            self.assertTrue(MusicMixin._pc_cmd(engine,'Открой Safari'))
        engine._say_quick.assert_called_once_with('Запуск запрошен','Открой Safari')
        with patch.object(tools,'t_control_mac_app',side_effect=mc.AppNotFound('unknown')):
            self.assertFalse(MusicMixin._pc_cmd(engine,'Открой неизвестное'))


if __name__=='__main__':unittest.main()
