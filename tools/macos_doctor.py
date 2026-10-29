"""Локальная диагностика прототипа: зависимости, инструменты разработки и подпись.

Не читает токены и не выполняет запросов к моделям. Библиотеки проверяются в отдельных процессах.
"""
import json
import platform
import shutil
import subprocess
import sys


def main():
    report = {"system": platform.system(), "macos": platform.mac_ver()[0], "arch": platform.machine(),
              "python": platform.python_version(), "libraries": {}, "tools": {}, "signing_identity": False}
    for module in ("numpy", "onnxruntime", "vosk", "onnx_asr", "piper", "httpx", "websockets", "webview", "sounddevice", "psutil", "yt_dlp", "AVFoundation"):
        try:
            run = subprocess.run([sys.executable, "-c", "import " + module], capture_output=True, text=True, timeout=20)
            report["libraries"][module] = {"ok": run.returncode == 0, "error": "" if run.returncode == 0 else run.stderr[-300:]}
        except subprocess.TimeoutExpired:
            report["libraries"][module] = {"ok": False, "error": "timeout"}
    for name in ("git", "python3", "codex", "claude"):
        path = shutil.which(name)
        try:
            ok = bool(path) and subprocess.run([path, "--version"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        report["tools"][name] = {"path": path, "runs": ok}
    if sys.platform == "darwin":
        run = subprocess.run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
                             capture_output=True, text=True, timeout=10)
        import re
        match = re.search(r"(\d+) valid identities found", run.stdout)
        report["signing_identity"] = bool(match and int(match[1]))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(entry["ok"] for entry in report["libraries"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
