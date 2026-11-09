"""Voice settings, persistence and routing. No real desktop or network actions."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import voice_commands as vc
import tools
from settings import Settings
from engine_music import MusicMixin
from engine_commands import CommandsMixin


class VoiceCommandsTest(unittest.TestCase):
    def test_requested_aliases(self):
        for target, words in {
            'Claude': ['Клод', 'клод код'], 'com.openai.codex': ['кодекс', 'чатджипити', 'джипити'],
            'Dewiar': ['агент', 'ассистент', 'помощник'], 'Cursor': ['курсор', 'редактор код'],
            'ru.yandex.desktop.music': ['музыка', 'песни'], 'WhatsApp': ['WhatsApp'], 'DeepSeek Harness': ['дипсиик'],
            'Code': ['вскод', 'вс код'],
            'com.apple.iWork.Numbers': ['таблица', 'таблицу', 'таблицы', 'Numbers'],
            'org.libreoffice.script': ['офис', 'LibreOffice', 'LinbreOffice'],
            'com.apple.mail': ['почта', 'почту', 'mail'],
            'com.apple.AddressBook': ['контакты', 'контакт', 'Contacts'],
        }.items():
            for word in words:
                with self.subTest(word=word):self.assertEqual(vc.parse('открой ' + word)['application'], target)
        vscode = next(a for a in vc.DEFAULT['applications'] if a['target'] == 'Code')
        self.assertNotIn('код', vscode['aliases'])

    def test_dnd_literal_commands_are_explicit_and_not_substring_matches(self):
        for phrase in ['не беспокоить', 'переговоры', 'включи режим не беспокоить', 'Пожалуйста, не беспокоить', 'переговоры, пожалуйста']:
            self.assertEqual(vc.parse(phrase)['action'], 'dnd_on', phrase)
        self.assertEqual(vc.parse('выключи не беспокоить')['action'], 'dnd_off')
        for phrase in ['запланируй переговоры завтра', 'что значит не беспокоить', 'у меня переговоры завтра', 'не включай режим не беспокоить']:
            self.assertIsNone(vc.parse(phrase), phrase)
        c = vc.config(); next(a for a in c['actions'] if a['id'] == 'dnd_on')['enabled'] = False
        self.assertFalse(vc.parse('переговоры', c)['enabled'])
        self.assertIsNone(vc.parse('таблица'))
        self.assertIsNone(vc.parse('открой таблицу из файла'))

    def test_numbers_missing_names_the_app_instead_of_bundle_id(self):
        import mac_control
        with self.assertRaisesRegex(mac_control.AppNotFound, '«Numbers»'):
            mac_control.resolve('com.apple.iWork.Numbers', [])

    def test_new_defaults_migrate_without_replacing_user_choices(self):
        c = vc.config(); c.pop('defaults_revision')
        c['applications'] = [a for a in c['applications'] if a['target'] not in vc.NEW_APPLICATIONS]
        c['actions'] = [a for a in c['actions'] if a['id'] not in vc.LITERAL_ACTIONS]
        c['applications'][0]['aliases'] = ['мой клод']
        next(a for a in c['applications'] if a['name'] == 'Cursor')['aliases'].append('офис')
        migrated = vc.validate(c)
        self.assertEqual(vc.parse('открой офис', migrated)['application'], 'Cursor')
        self.assertEqual(vc.parse('открой таблицу', migrated)['application'], 'com.apple.iWork.Numbers')
        self.assertEqual(vc.parse('переговоры', migrated)['action'], 'dnd_on')
        self.assertEqual(vc.target('клод', migrated), 'клод')
        migrated['applications'] = [a for a in migrated['applications'] if a['target'] != 'com.apple.iWork.Numbers']
        self.assertEqual(vc.target('таблица', vc.config(migrated)), 'таблица')

    def test_focus_routes_without_model_and_obeys_tool_permissions(self):
        c = vc.config()
        engine = SimpleNamespace(s=SimpleNamespace(get=lambda *args: c), toolbox=Mock(), _say_quick=Mock())
        engine.toolbox.policy.return_value = (True, False)
        with patch.object(tools, 'MAC', True), patch.object(tools, 't_control_mac_app', return_value='Включено') as run:
            self.assertTrue(MusicMixin._pc_cmd(engine, 'переговоры'))
            self.assertEqual(run.call_args.args[0], {'action': 'dnd_on'})
            run.reset_mock()
            next(a for a in c['actions'] if a['id'] == 'dnd_on')['enabled'] = False
            self.assertTrue(MusicMixin._pc_cmd(engine, 'переговоры'))
            run.assert_not_called()

    def test_focus_preview_and_validation_do_not_change_system(self):
        c = vc.config()
        engine = SimpleNamespace(s=SimpleNamespace(get=lambda *args: c), toolbox=Mock())
        engine.toolbox.policy.return_value = (True, False)
        with patch('mac_focus.set_dnd') as run:
            result = CommandsMixin.command(engine, 'voice_command_test', {'text': 'переговоры'})
            self.assertEqual(result['action'], 'dnd_on'); run.assert_not_called()
        next(a for a in c['actions'] if a['id'] == 'dnd_on')['phrases'] = ['режим {приложение}']
        with self.assertRaisesRegex(ValueError, 'без подстановок'):vc.validate(c)

    def test_specific_song_template_precedes_generic_open(self):
        song = 'Земфира — Хочешь & Live 2026'
        for verb in ['найди', 'открой', 'включи']:
            result = vc.parse(verb + ' песню ' + song)
            self.assertEqual(result['action'], 'yandex_search'); self.assertEqual(result['query'], song)
        self.assertEqual(vc.parse('открой музыку')['application'], 'ru.yandex.desktop.music')
        self.assertEqual(vc.parse('найди песню Пожалуйста')['query'], 'Пожалуйста')

    def test_song_processing_phrases_extract_query(self):
        song = 'Папа тоже плачет Фролова'
        for command in ('найти песню', 'поиск песни', 'поискать песню', 'обработать песню', 'обработай песню'):
            with self.subTest(command=command):
                result = vc.parse(f'{command} {song}')
                self.assertEqual(result['action'], 'song_process')
                self.assertEqual(result['query'], song)

    def test_song_processing_command_creates_card_and_starts_background_batch(self):
        row = {'song_id': 'song-id'}
        store, sources = Mock(), Mock()
        store.create.return_value = row
        engine = SimpleNamespace(
            s=SimpleNamespace(get=lambda *args: vc.config()),
            song_store=store, song_sources=sources, _say_quick=Mock())
        with patch.object(tools, 'MAC', False):
            self.assertTrue(MusicMixin._pc_cmd(engine, 'найти песню Папа тоже плачет Фролова'))
        store.create.assert_called_once_with('Папа тоже плачет Фролова', source='query')
        sources.start.assert_called_once_with('song-id', process=True)
        self.assertIn('Папа тоже плачет Фролова', engine._say_quick.call_args.args[0])

    def test_old_voice_config_migrates_song_processing_action(self):
        old = vc.config()
        old.pop('defaults_revision')
        old['actions'] = [a for a in old['actions'] if a['id'] != 'song_process']
        migrated = vc.validate(old)
        self.assertEqual(vc.parse('обработать песню Ленинград МГА', migrated)['action'], 'song_process')

    def test_edit_remove_disable_and_validate(self):
        c = vc.config(); self.assertEqual(vc.validate(c), c)
        c['actions'][0]['phrases'] = ['покажи мне {приложение}']
        self.assertIsNone(vc.parse('открой клод', c))
        self.assertEqual(vc.parse('покажи мне клод', c)['application'], 'Claude')
        c['actions'][0]['enabled'] = False
        self.assertFalse(vc.parse('покажи мне клод', c)['enabled'])
        c['actions'][0]['phrases'] = ['закрой {приложение}']
        with self.assertRaisesRegex(ValueError, 'Повторяющаяся'):vc.validate(c)
        c = vc.config(); c['applications'][1]['aliases'].append('клод')
        with self.assertRaisesRegex(ValueError, 'разным приложениям'):vc.validate(c)
        c = vc.config(); c['actions'][0]['phrases'] = ['открой .*']
        with self.assertRaises(ValueError):vc.validate(c)

    def test_settings_save_reload_and_reject_invalid_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'; settings = Settings(path)
            c = vc.config(); c['applications'][0]['aliases'] = ['мой клод']
            settings.update({'voice_commands': c})
            self.assertEqual(Settings(path).get('voice_commands'), c)
            self.assertEqual(vc.parse('открой мой клод', Settings(path).get('voice_commands'))['application'], 'Claude')
            before = path.read_bytes(); bad = copy.deepcopy(c); bad['actions'][0]['phrases'] = []
            with self.assertRaises(ValueError):settings.update({'voice_commands': bad})
            self.assertEqual(path.read_bytes(), before)

    def test_preview_never_executes_and_reports_permission(self):
        engine = SimpleNamespace(s=SimpleNamespace(get=lambda *args: {}), toolbox=Mock())
        engine.toolbox.policy.return_value = (True, True)
        with patch.object(tools, 't_control_mac_app') as run, patch.object(tools, 't_yandex_music') as music:
            result = CommandsMixin.command(engine, 'voice_command_test', {'text': 'Закрой клод'})
            self.assertEqual(result['application'], 'Claude'); self.assertTrue(result['confirm'])
            run.assert_not_called(); music.assert_not_called()

    def test_music_routing_exact_title_and_disabled_action(self):
        c = vc.config()
        engine = SimpleNamespace(s=SimpleNamespace(get=lambda *args: c), toolbox=Mock(), _say_quick=Mock(), _yandex_cmd=Mock(return_value=True))
        engine.toolbox.policy.return_value = (True, False)
        self.assertTrue(MusicMixin._pc_cmd(engine, 'Включи песню Любимую музыку'))
        engine._yandex_cmd.assert_called_once_with('Любимую музыку', 'Включи песню Любимую музыку', exact=True)
        engine._yandex_cmd.reset_mock(); c['actions'][-1]['enabled'] = False
        self.assertTrue(MusicMixin._pc_cmd(engine, 'Включи песню Любимую музыку'))
        engine._yandex_cmd.assert_not_called()

    def test_yandex_native_detection_and_encoded_query(self):
        row = {'id': 'ru.yandex.desktop.music', 'name': 'Яндекс Музыка'}
        with patch.object(tools, 'DRY', None), patch.object(tools, 'MAC', True), patch.object(tools, 'desktop_apps', return_value=[row]), \
             patch.object(tools, 'launch') as launch:
            self.assertTrue(tools.yandex_music_installed())
            tools.t_yandex_music({'query': 'Кино & Live'}, {})
            self.assertIn('yandexmusic://search?text=%D0%9A%D0%B8%D0%BD%D0%BE%20%26%20Live', launch.call_args.args[0])

    def test_removed_alias_not_restored_by_native_resolver(self):
        import mac_control
        c = vc.config(); c['applications'][0]['aliases'] = ['мой клод']
        rows = [{'name': 'Claude', 'id': 'claude'}]
        self.assertEqual(mac_control.resolve('мой клод', rows, c)['name'], 'Claude')
        with self.assertRaises(mac_control.AppNotFound):mac_control.resolve('клод', rows, c)

    def test_packaged_dewiar_is_discovered_outside_applications(self):
        import macsys
        import plistlib
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'Dewiar.app'; (bundle / 'Contents').mkdir(parents=True)
            (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleName': 'Dewiar', 'CFBundleIdentifier': 'pro.dewiar.macos'}))
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(bundle / 'Contents/MacOS/Dewiar')), \
                 patch.object(Path, 'glob', return_value=[]):
                rows = macsys.applications()
            self.assertEqual(rows[0]['path'], str(bundle))

    def test_custom_command_confirmation_cannot_execute_on_no(self):
        c = vc.config(); c['actions'][1]['phrases'] = ['попрощайся с {приложение}']
        engine = SimpleNamespace(s=SimpleNamespace(get=lambda *args: c), toolbox=Mock(), _say_quick=Mock(), confirm=Mock())
        engine.toolbox.policy.return_value = (True, True)
        def inline_thread(**args):return SimpleNamespace(start=args['target'])
        with patch.object(tools, 'MAC', True), patch('engine_music.threading.Thread', side_effect=inline_thread), \
             patch.object(tools, 't_control_mac_app', return_value='Закрыто') as execute:
            engine.confirm.return_value = False
            self.assertTrue(MusicMixin._pc_cmd(engine, 'попрощайся с клод')); execute.assert_not_called()
            engine.confirm.return_value = True
            self.assertTrue(MusicMixin._pc_cmd(engine, 'попрощайся с клод'))
            self.assertEqual(execute.call_args.args[0], {'action': 'quit', 'application': 'Claude'})


if __name__ == '__main__':unittest.main()
