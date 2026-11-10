"""Registered, local project backup scripts used by the assistant's task workflow.

Only scripts listed here can be run. User text is resolved to a project ID and is
never used as a filesystem path or shell command.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path


PROJECT_BACKUPS = {
    "cmms-rus": {
        "title": "CMMS-rus",
        "aliases": ("cmms-rus", "cmms", "dc master", "dcmaster", "дсмастер", "дс мастер"),
        "root": Path.home() / "Documents/cmms-rus",
        "script": Path("_utilities/backup_cmms_rus.py"),
        "python": "python3",
        "args": (),
        "scope_note": "Архив содержит исходники по правилам сценария; database, media и другие исключённые им данные не копируются.",
    },
}


def normalize(value):
    return re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()


def resolve_project(value):
    name = normalize(value)
    for project_id, spec in PROJECT_BACKUPS.items():
        if name in {normalize(alias) for alias in spec["aliases"]}:
            return project_id
    raise ValueError("Не узнал проект. Подключённые проекты для бэкапа: " + ", ".join(PROJECT_BACKUPS) + ".")


def run_project_backup(project_id, *, cancel=None, on_progress=None):
    """Run a fixed registered script; return its captured output and exit code."""
    spec = PROJECT_BACKUPS.get(str(project_id or ""))
    if not spec:
        raise ValueError("Сценарий бэкапа для этого проекта не зарегистрирован.")
    root = Path(spec["root"])
    script_rel = Path(spec["script"])
    try:
        root = root.resolve(strict=True)
        script = (root / script_rel).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Не найден сценарий или каталог проекта {spec['title']}: {exc}") from exc
    if not root.is_dir() or script != root / script_rel or not script.is_file():
        raise ValueError(f"Путь сценария проекта {spec['title']} не совпадает с реестром; запуск отменён.")

    python = shutil.which(str(spec.get("python") or "python3"))
    if not python and spec.get("python", "python3") == "python3" and Path("/usr/bin/python3").is_file():
        python = "/usr/bin/python3"
    if not python:
        raise ValueError(f"Не найден интерпретатор Python для сценария {spec['title']}.")

    try:
        proc = subprocess.Popen([python, "-u", str(script), *spec.get("args", ())], cwd=str(root),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as exc:
        raise ValueError(f"Не удалось запустить сценарий {spec['title']}: {exc}") from exc

    started = time.monotonic()
    output = ""
    while True:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            try:
                partial, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                partial, _ = proc.communicate()
            raise InterruptedError("Создание резервной копии остановлено пользователем." + ("\n" + partial if partial else ""))
        try:
            output, _ = proc.communicate(timeout=5)
            break
        except subprocess.TimeoutExpired as exc:
            if time.monotonic() - started >= 1800:
                proc.kill()
                partial, _ = proc.communicate()
                raise TimeoutError("Создание архива превысило 30 минут и было остановлено." + ("\n" + partial if partial else ""))
            if on_progress:
                on_progress(round(time.monotonic() - started))
            # communicate() can be retried after TimeoutExpired; it preserves buffered output.
            if exc.output:
                output = exc.output if isinstance(exc.output, str) else exc.output.decode("utf-8", "replace")
    return {"returncode": proc.returncode, "output": output or ""}


def describe_result(project_id, output):
    """Extract the stable human-facing facts printed by the registered script."""
    spec = PROJECT_BACKUPS[project_id]
    def line(pattern):
        match = re.search(pattern, output, re.MULTILINE)
        return match.group(1).strip() if match else ""

    return {
        "project": spec["title"],
        "included": line(r"^Included files:\s*(.+)$"),
        "excluded": line(r"^Excluded paths:\s*(.+)$"),
        "archive": line(r"^Created:\s*(.+)$").split(" (", 1)[0],
        "size": line(r"^Created:.*\(([^()]*)\)$"),
        "sha256": line(r"^SHA-256:\s*(.+)$"),
        "manifest": line(r"^Manifest:\s*(.+)$"),
        "scope_note": spec.get("scope_note", ""),
    }
