"""Vendor block must not disable local models or subscription providers. No live network."""
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import egress
import httpx


class VendorServices(unittest.TestCase):
    def setUp(self):
        self.settings = Mock()
        self.settings.get.return_value = False
        self.old_settings = egress._state["settings"]
        egress._state["settings"] = self.settings

    def tearDown(self):
        egress._state["settings"] = self.old_settings

    def test_domains_and_subdomains(self):
        for host in ('dewiar.pro', 'api.dewiar.pro', 'DEWIAR.PRO.', b'dewiar.ru', 'api.dewiar.ru'):
            with self.subTest(host=host), self.assertRaises(egress.DewiarServicesDisabled):
                egress.check_dewiar_host(host)
        for host in ('127.0.0.1', 'localhost', 'chatgpt.com', 'api.openai.com', 'claude.ai', 'api.anthropic.com', 'huggingface.co', 'dewiar.pro.example.com'):
            egress.check_dewiar_host(host)

    def test_explicit_opt_in(self):
        self.settings.get.return_value = True
        egress.check_dewiar_host('dewiar.pro')

    def test_dns_and_http_block_before_transport(self):
        resolver, sender = Mock(return_value=[]), Mock()
        # Restore global hooks after this isolated test.
        with patch.object(socket, 'getaddrinfo', resolver), patch.object(socket, 'socket', type('FixtureSocket', (), {'connect': Mock(), 'connect_ex': Mock()})), patch.object(httpx.Client, 'send', sender), patch.object(egress, '_saver'), patch.dict(egress._state, installed=False):
            egress.install(self.settings)
            with self.assertRaises(egress.DewiarServicesDisabled):
                socket.getaddrinfo('dewiar.pro', 443)
            resolver.assert_not_called()
            socket.getaddrinfo('localhost', 11434)
            resolver.assert_called_once()
            # Construct client before fixture socket could be needed by an HTTP transport.
            with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
                with self.assertRaises(egress.DewiarServicesDisabled):
                    client.get('https://dewiar.pro/dwrapp/cloud.php')
                sender.assert_not_called()
                client.get('http://127.0.0.1:11434/api/tags')
                client.get('https://api.openai.com/v1/models')
                client.get('https://api.anthropic.com/v1/models')
                self.assertEqual(sender.call_count, 3)

    def test_announcements_do_not_fetch(self):
        from engine import Engine
        engine = Engine.__new__(Engine)
        engine._ann = [{'id': 1}]
        with patch('news.announcements') as fetch:
            engine._ann_fetch()
            fetch.assert_not_called()
            self.assertEqual(engine._ann, [])


if __name__ == '__main__':
    unittest.main()
