"""Explicit folder-access probes in the core process; never scans contents or changes a workspace."""
import os
from pathlib import Path


def folder_targets(workspace):
    home = Path.home()
    return {"workspace": workspace.root, "documents": home / "Documents",
            "desktop": home / "Desktop", "downloads": home / "Downloads"}


def folder_access(workspace, target="", selected=""):
    paths = folder_targets(workspace)
    if not target:
        return {key: {"path": str(path), "state": "unchecked", "readable": False} for key, path in paths.items()}
    if target == "selected":
        if not selected or not Path(selected).is_absolute():
            raise ValueError("Выберите папку в системном окне")
        path = Path(selected)
    elif target in paths:
        path = paths[target]
    else:
        raise ValueError("Неизвестная папка для проверки доступа")
    result = {"target": target, "path": str(path), "readable": False}
    try:
        # Opening an iterator is enough to exercise the actual process' permission.
        # No file names, file contents or write operations leave this function.
        with os.scandir(path) as entries:
            next(entries, None)
        return dict(result, state="authorized", readable=True, writable_hint=os.access(path, os.W_OK))
    except PermissionError:
        return dict(result, state="denied", error="macOS не разрешила ядру Dewiar читать эту папку")
    except FileNotFoundError:
        return dict(result, state="missing", error="Папка не найдена; проверьте путь или подключение диска/iCloud")
    except (OSError, ValueError) as error:
        return dict(result, state="error", error=str(error))
