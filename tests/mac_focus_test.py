"""Focus uses explicit native shortcuts, no private preferences and no real settings changes."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
import mac_focus
import mac_control


class FocusTest(unittest.TestCase):
    def test_on_off_and_repeated_on_never_toggle(self):
        names = '\n'.join(mac_focus.SHORTCUTS.values())
        def run(args, **kw):
            return Mock(returncode=0, stdout=names if args[1] == 'list' else '')
        with patch.object(mac_focus.subprocess, 'run', side_effect=run) as execute:
            mac_control.execute({'action': 'dnd_on'})
            mac_control.execute({'action': 'dnd_on'})
            result = mac_control.execute({'action': 'dnd_off'})
            invoked = [c.args[0] for c in execute.call_args_list if c.args[0][1] == 'run']
            self.assertEqual(invoked, [['/usr/bin/shortcuts','run',mac_focus.SHORTCUTS[v]] for v in (True,True,False)])
            self.assertFalse(result['enabled'])

    def test_missing_or_duplicate_shortcut_does_not_run(self):
        for names in ['', '\n'.join([mac_focus.SHORTCUTS[True]] * 2)]:
            with patch.object(mac_focus.subprocess, 'run', return_value=Mock(returncode=0,stdout=names)) as execute:
                with self.assertRaisesRegex(ValueError, 'Нужна одна системная команда'):mac_focus.set_dnd(True)
                self.assertEqual(execute.call_count, 1)

    def test_native_error_and_timeout_are_not_success_or_retried(self):
        for error in [Mock(returncode=1, stdout=''), subprocess.TimeoutExpired('shortcuts',30)]:
            with patch.object(mac_focus.subprocess, 'run', side_effect=[Mock(returncode=0,stdout=mac_focus.SHORTCUTS[True]), error]) as execute:
                with self.assertRaises(ValueError):mac_focus.set_dnd(True)
                self.assertEqual(execute.call_count, 2)


if __name__ == '__main__':unittest.main()
