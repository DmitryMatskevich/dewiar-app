"""No-fallback regression. Isolated state, synthetic output, all providers mocked."""
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TEMP = tempfile.TemporaryDirectory(prefix='dewiar-imagegen-')
os.environ['DEWIAR_CONFIG_DIR'] = str(Path(TEMP.name) / 'config')
os.environ['DEWIAR_STATE_DIR'] = str(Path(TEMP.name) / 'state')
ROOT = Path(os.environ.get('DEWIAR_IMAGEGEN_ROOT', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'core'))
import imagegen

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')


class NoFallback(unittest.TestCase):
    def setUp(self):
        self.output = tempfile.TemporaryDirectory(dir=TEMP.name)
        self.addCleanup(self.output.cleanup)
        self.folder = Path(self.output.name)
        self.settings = Mock()
        self.settings.get.return_value = {'profiles': {'codex': {
            'kind': 'codex_subscription', 'roles': ['images'], 'priority': 1}}}
        self.workspace = SimpleNamespace(safe=lambda name: self.folder / name)
        self.provider = Mock(return_value=base64.b64encode(PNG).decode())
        patches = [patch.dict(sys.modules, {'subscriptions': SimpleNamespace(generate_image=self.provider)}),
                   patch('imagegen.egress.local_only', return_value=False),
                   patch('modelrouter.in_schedule', return_value=True),
                   patch('modelrouter.blocked', return_value=False),
                   patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def generate(self):
        return imagegen.generate(self.settings, self.workspace, 'ТЕСТ — отдельные данные')

    def test_subscription_success_never_selects_api(self):
        with patch('imagegen._api_image') as api, patch('imagegen._openai_profile') as profile:
            result = self.generate()
            self.assertEqual(Path(result['path']).read_bytes(), PNG)
            self.assertEqual(result['provider'], 'Подписка Codex')
            api.assert_not_called(); profile.assert_not_called()

    def test_subscription_error_never_selects_or_calls_api(self):
        cause = RuntimeError('synthetic provider failure')
        self.provider.side_effect = cause
        with patch('imagegen._api_image') as api, patch('imagegen._openai_profile') as profile:
            with self.assertRaisesRegex(RuntimeError, 'переход на OpenAI API отключён') as caught:
                self.generate()
            self.assertIs(caught.exception.__cause__, cause)
            api.assert_not_called(); profile.assert_not_called()
            self.assertEqual(list(self.folder.iterdir()), [])

    def test_invalid_subscription_payload_does_not_use_api(self):
        self.provider.return_value = '@@invalid@@'
        with patch('imagegen._api_image') as api:
            with self.assertRaisesRegex(RuntimeError, 'переход на OpenAI API отключён'):
                self.generate()
            api.assert_not_called()

    def test_empty_subscription_image_does_not_use_api(self):
        self.provider.return_value = ''
        with patch('imagegen._api_image') as api:
            with self.assertRaises(RuntimeError): self.generate()
            api.assert_not_called()

    def test_unavailable_assigned_subscription_does_not_use_api(self):
        for schedule, blocked in [(False, False), (True, True)]:
            with self.subTest(schedule=schedule, blocked=blocked), \
                    patch('modelrouter.in_schedule', return_value=schedule), \
                    patch('modelrouter.blocked', return_value=blocked), \
                    patch('imagegen._api_image') as api, patch('imagegen._openai_profile') as profile:
                with self.assertRaisesRegex(RuntimeError, 'переход на OpenAI API отключён'):
                    self.generate()
                api.assert_not_called(); profile.assert_not_called(); self.provider.assert_not_called()

    def test_api_only_route_still_works_without_codex(self):
        self.settings.get.return_value = {'profiles': {}}
        with patch('imagegen._api_image', return_value=PNG) as api, \
                patch('imagegen._openai_profile', return_value={'id': 'test-api'}):
            result = self.generate()
            self.assertEqual(result['provider'], 'OpenAI API')
            api.assert_called_once(); self.provider.assert_not_called()

    def test_api_error_is_not_retried(self):
        self.settings.get.return_value = {'profiles': {}}
        with patch('imagegen._api_image', side_effect=RuntimeError('API failure')) as api, \
                patch('imagegen._openai_profile', return_value={'id': 'test-api'}):
            with self.assertRaisesRegex(RuntimeError, 'API failure'): self.generate()
            api.assert_called_once(); self.provider.assert_not_called()

    def test_local_mode_never_selects_any_provider(self):
        with patch('imagegen.egress.local_only', return_value=True), \
                patch('imagegen._codex_profile') as codex, patch('imagegen._openai_profile') as profile:
            with self.assertRaisesRegex(RuntimeError, 'локальном режиме'): self.generate()
            codex.assert_not_called(); profile.assert_not_called(); self.provider.assert_not_called()


if __name__ == '__main__':
    try: unittest.main()
    finally: TEMP.cleanup()
