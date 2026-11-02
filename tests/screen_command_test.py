"""Standalone capture routing, permissions, confirmation and cancellation, without an LLM."""
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from engine_commands import CommandsMixin


class CommandTests(unittest.TestCase):
    def engine(self,policy=(True,False)):
        e=types.SimpleNamespace(toolbox=types.SimpleNamespace(policy=Mock(return_value=policy)),_say_quick=Mock(),
            _turn_lock=threading.Lock(),turn_n=0,turn=None,emit=Mock(),history=[],s={'persona':'lira'},workspace=object(),note=Mock(),_conv_after=Mock())
        e.finished=threading.Event()
        def state(s):
            if s=='idle':e.finished.set()
        e.set_state=Mock(side_effect=state)
        def cancel():
            if e.turn:e.turn.cancel.set()
        e._cancel_locked=cancel
        def speak(turn,q):
            while True:
                item=q.get()
                if item is None:return
                if isinstance(item,tuple):item[1].set()
        e._speak_worker=speak
        e._confirm=Mock(return_value=True)
        return e

    def test_explicit_command_runs_once_without_model(self):
        e=self.engine()
        with patch('tools.t_save_screenshot',return_value='Документ готов') as run:
            self.assertTrue(CommandsMixin._screenshot_cmd(e,'Лира, снимок экрана'))
            self.assertTrue(e.finished.wait(3))
        run.assert_called_once()
        self.assertIs(run.call_args.args[1]['workspace'],e.workspace)
        self.assertEqual(e.history[-1]['content'],'Документ готов')
        self.assertIsNone(e.turn)

    def test_disabled_and_declined_never_capture(self):
        for policy in [(False,False),(True,True)]:
            e=self.engine(policy);e._confirm.return_value=False
            with patch('tools.t_save_screenshot') as run:
                self.assertTrue(CommandsMixin._screenshot_cmd(e,'Скриншот экрана'))
                if policy[0]:self.assertTrue(e.finished.wait(3))
                run.assert_not_called()
        self.assertIn('отменено',e.history[-1]['content'])

    def test_confirmed_capture_and_error(self):
        e=self.engine((True,True))
        with patch('tools.t_save_screenshot',side_effect=ValueError('Нет доступа')) as run:
            CommandsMixin._screenshot_cmd(e,'Сделай скриншот')
            self.assertTrue(e.finished.wait(3))
        e._confirm.assert_called_once();run.assert_called_once()
        self.assertIn('Нет доступа',e.history[-1]['content'])
        self.assertFalse(any(c.kwargs.get('status')=='done' for c in e.emit.call_args_list))

    def test_stop_prevents_late_answer(self):
        e=self.engine();entered=threading.Event();release=threading.Event()
        def run(a,ctx):
            entered.set();release.wait(3)
            self.assertTrue(ctx['screenshot_cancelled']())
            return 'снимок сохранён'
        with patch('tools.t_save_screenshot',side_effect=run):
            CommandsMixin._screenshot_cmd(e,'Сделай снимок экрана')
            self.assertTrue(entered.wait(3));e.turn.cancel.set();release.set()
            self.assertTrue(e.finished.wait(3))
        self.assertFalse(e.history)
        self.assertFalse(any(c.args[0]=='answer' for c in e.emit.call_args_list))

    def test_unrelated_question_not_intercepted(self):
        e=self.engine()
        self.assertFalse(CommandsMixin._screenshot_cmd(e,'Как сделать снимок экрана?'))
        e.toolbox.policy.assert_not_called()

    def test_named_application_is_passed_to_capture_and_confirmation(self):
        e=self.engine((True,True))
        with patch('tools.t_save_screenshot',return_value='Снимок готов') as run:
            self.assertTrue(CommandsMixin._screenshot_cmd(e,'Лира, снимок экрана приложения Клод'))
            self.assertTrue(e.finished.wait(3))
        self.assertEqual(run.call_args.args[0],{'source':'application','application':'Клод'})
        self.assertIn('Клод',e._confirm.call_args.args[1])

    def test_text_only_mac_can_capture_but_headless_server_cannot(self):
        import tools
        class S:
            def get(self,*keys):
                data={'tools':{'enabled':{'save_screenshot':True},'confirm':{'save_screenshot':False}}}
                for key in keys:
                    data=data.get(key) if isinstance(data,dict) else None
                return data
        box=tools.Toolbox(S(),None);box.hooks['server']=True
        with patch('tools.MAC',True),patch.dict(tools.os.environ,{'DEWIAR_MAC_DESKTOP':'1'}):
            self.assertEqual(box.policy('save_screenshot'),(True,False))
        with patch.dict(tools.os.environ,{'DEWIAR_MAC_DESKTOP':'0'}):
            self.assertEqual(box.policy('save_screenshot'),(False,False))


if __name__=='__main__':unittest.main()
