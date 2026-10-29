"""Native macOS recovery menu is supplied through pywebview for every window."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "core"), str(ROOT / "ui")]


class MacMenu(unittest.TestCase):
    def test_webview_menu_spec_contains_recovery_actions(self):
        from macshell import make_macos_menu

        api = Mock()
        menus = make_macos_menu(api)
        self.assertEqual([menu.title for menu in menus], ["__app__", "Агент Dewiar"])
        self.assertEqual(
            [item.title for item in menus[0].items[1:]],
            ["Показать агента", "Открыть настройки", "Модели"],
        )
        for item in menus[0].items[1:]:
            item.function()
        api.from_tray.assert_called_once_with()
        api.open_settings.assert_any_call()
        api.open_settings.assert_any_call("brain")


if __name__ == "__main__":
    unittest.main()
