"""Платформа Dewiar и пользовательские пути; без побочных эффектов при импорте.

Настройки и память на Mac — в Application Support, а не в очищаемом Caches.
Переменные переопределения обрабатывает settings.py.
"""
import os
import platform
import sys
from pathlib import Path

WIN = os.name == "nt"
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")


def data_paths(home=None):
    home = Path(home) if home is not None else Path.home()
    if WIN:
        return (Path(os.environ.get("APPDATA") or home / "AppData/Roaming") / "Dewiar",
                Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local") / "Dewiar")
    if MAC:
        config = home / "Library/Application Support/Dewiar"
        return config, config / "state"
    return home / ".config/dewiar", home / ".local/share/dewiar"


def macos_name():
    return "macOS " + (platform.mac_ver()[0] or platform.release())
