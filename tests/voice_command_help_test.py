"""Conversational voice settings: guidance, proposals and mandatory confirmation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import guide
import tools
import voice_commands as vc
import voice_command_help as helper
from settings import Settings
from engine_tools import ToolsMixin
from engine_music import MusicMixin


class VoiceHelpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(Path(self.tmp.name) / 'settings.json')
        self.settings.update({'tools': {'confirm': {'voice_command_apply': False}}})
        self.engine = SimpleNamespace(s=self.settings, emit=Mock(), _confirm=Mock(return_value=False))
        self.box = tools.Toolbox(self.settings, None); self.box.hooks['engine'] = self.engine
        self.engine.toolbox = self.box

    def proposal(self, **args):
        return json.loads(tools.t_voice_command_settings({'operation': 'propose', 'change': 'add_alias', 'application': 'Telegram', 'alias': 'телега', **args}, {'engine': self.engine}))

    def test_guide_injected_for_help_and_trouble_queries(self):
        for query in ['Как настроить голосовые команды?', 'Почему ты не понимаешь мою команду?', 'Помоги добавить название приложения']:
            with self.subTest(query=query):
                self.assertEqual(guide.search(query, 'pc')[0]['id'], 'voice-commands')
                hint = guide.hint(query, 'pc')
                for text in ['Голосовые команды', 'voice_command_settings', 'voice_command_apply', 'после отказа']:
                    self.assertIn(text.lower(), hint.lower())

    def test_proposal_does_not_save_or_execute_desktop(self):
        before = self.settings.path.read_bytes()
        with patch.object(tools, 'desktop_apps', return_value=[]), patch.object(tools, 't_control_mac_app') as desktop:
            result = self.proposal()
        self.assertFalse(result['applied']); self.assertIn('телега', result['summary'])
        self.assertEqual(before, self.settings.path.read_bytes()); desktop.assert_not_called()

    def test_refusal_prevents_write_even_when_confirmation_checkbox_is_off(self):
        result = self.proposal(); before = self.settings.path.read_bytes()
        blocked, forced, question = self.box.guard('voice_command_apply', {'proposal_id': result['proposal_id']})
        self.assertIsNone(blocked); self.assertTrue(forced)
        self.assertIn('Telegram', question); self.assertIn('телега', question)
        turn = SimpleNamespace(asked='Помоги настроить название программы')
        answer = ToolsMixin._use_tool(self.engine, turn, {'name': 'voice_command_apply', 'args': {'proposal_id': result['proposal_id']}}, None)
        self.assertIn('отказался', answer); self.engine._confirm.assert_called_once()
        self.assertEqual(self.settings.path.read_bytes(), before)

    def test_consent_applies_one_change_preserving_other_settings(self):
        result = self.proposal(); self.engine._confirm.return_value = True
        before = self.settings.snapshot()
        answer = ToolsMixin._use_tool(self.engine, SimpleNamespace(asked='Добавь название'),
                                     {'name': 'voice_command_apply', 'args': {'proposal_id': result['proposal_id']}}, None)
        self.assertTrue(json.loads(answer)['applied'])
        self.assertEqual(vc.parse('Открой телега', self.settings.get('voice_commands'))['application'], 'Telegram')
        self.assertEqual(before['tools'], self.settings.get('tools'))
        self.assertEqual(before['audio'], self.settings.get('audio'))
        with self.assertRaises(ValueError):helper.apply(self.engine, result['proposal_id'])

    def test_stale_or_expired_proposal_cannot_overwrite_editor(self):
        result = self.proposal()
        config = vc.config(); config['actions'][0]['enabled'] = False
        self.settings.update({'voice_commands': config})
        with self.assertRaisesRegex(ValueError, 'изменились'):helper.apply(self.engine, result['proposal_id'])
        result = self.proposal(); self.engine._voice_command_proposals[result['proposal_id']]['created'] -= 601
        with self.assertRaisesRegex(ValueError, 'устарело'):helper.apply(self.engine, result['proposal_id'])

    def test_invalid_conflicting_and_empty_changes_rejected(self):
        with self.assertRaisesRegex(ValueError, 'разным приложениям'):self.proposal(alias='кодекс')
        with self.assertRaises(ValueError):self.proposal(application='Неизвестная программа')
        with self.assertRaises(ValueError):self.proposal(change='add_phrase', action='open', phrase='')

    def test_action_phrase_is_prepared_and_applied(self):
        result = self.proposal(change='add_phrase', action='minimize', phrase='спрячь окна {приложение}')
        helper.apply(self.engine, result['proposal_id'])
        request = vc.parse('спрячь окна клод', self.settings.get('voice_commands'))
        self.assertEqual(request['action'], 'minimize')

    def test_missing_known_app_offers_help_but_generic_other_commands_keep_routing(self):
        engine = SimpleNamespace(s=self.settings, toolbox=Mock(), _say_quick=Mock())
        engine.toolbox.policy.return_value = (True, False)
        with patch.object(tools, 'MAC', True), patch.object(tools, 't_control_mac_app', side_effect=ValueError('other')):
            self.assertTrue(MusicMixin._pc_cmd(engine, 'Открой клод'))
        from mac_control import AppNotFound
        engine._say_quick.reset_mock()
        with patch.object(tools, 'MAC', True), patch.object(tools, 't_control_mac_app', side_effect=AppNotFound('Не найдено приложение')):
            self.assertTrue(MusicMixin._pc_cmd(engine, 'Открой клод'))
            self.assertIn('Настроим?', engine._say_quick.call_args.args[0])
            self.assertFalse(MusicMixin._pc_cmd(engine, 'Открой рецепт'))


if __name__ == '__main__':unittest.main()
