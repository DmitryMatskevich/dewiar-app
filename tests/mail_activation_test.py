"""Startup tool catalogs must not read mail credentials or block on Keychain."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from mail_service import MailService
from tools import Toolbox


class MailActivationTest(unittest.TestCase):
    def test_startup_catalog_never_reads_account_secrets(self):
        mail = MailService.__new__(MailService)
        mail._is_activated = False
        mail.accounts = Mock(side_effect=AssertionError('Startup accessed credentials'))
        self.assertFalse(mail.configured())
        settings = SimpleNamespace(get=lambda *args: {})
        toolbox = Toolbox(settings, None)
        toolbox.hooks['mail'] = mail
        self.assertFalse(toolbox.policy('send_email')[0])
        self.assertFalse(toolbox.policy('read_emails')[0])
        toolbox.catalog()
        mail.accounts.assert_not_called()

    def test_opened_mail_uses_actual_account_configuration(self):
        mail = MailService.__new__(MailService)
        mail._is_activated = True
        mail.accounts = Mock(return_value=[{'enabled': True}])
        self.assertTrue(mail.configured())
        mail.accounts.assert_called_once()
        mail.accounts.return_value = [{'enabled': False}]
        self.assertFalse(mail.configured())


if __name__ == '__main__':unittest.main()
