"""Проверка реального Cocoa-окна и запуска ядра в изолированной временной папке.

Не использует аккаунты, микрофон и пользовательскую память. Запускайте Python из .venv.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="dewiar-mac-smoke-") as folder:
        tmp = Path(folder)
        config = tmp / "config"
        config.mkdir()
        (config / "settings.json").write_text(json.dumps({
            "sync": {"enabled": False}, "privacy": {"local_only": True, "mic": "button"},
            "obsidian": {"vault": ""}, "ui": {"welcome": "done"},
        }), encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        report = tmp / "window.json"
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("DEWIAR_")}
        env = dict(inherited, DEWIAR_CONFIG_DIR=str(config), DEWIAR_STATE_DIR=str(tmp / "state"),
                   DEWIAR_WORKSPACE_DIR=str(tmp / "workspace"), DEWIAR_PORT=str(port),
                   DEWIAR_MAC_SMOKE_REPORT=str(report), DEWIAR_NO_SCOPE="1", DEWIAR_PROVISION=str(tmp / "no-provision.json"))
        if "--features" in sys.argv:
            env["DEWIAR_MAC_SMOKE_FEATURES"] = "1"
        bundle = Path(sys.argv[sys.argv.index("--app") + 1]).resolve() if "--app" in sys.argv else root / "dist/Dewiar.app"
        command = ([str(bundle / "Contents/MacOS/Dewiar")] if "--bundle" in sys.argv
                   else [sys.executable, str(root / "ui/app.py")])
        result = subprocess.run(command + ["--text-only", "--settings"],
                                env=env, cwd=root, capture_output=True, text=True, timeout=75)
        log = tmp / "state/logs/mac-launch.log"
        if result.returncode or not report.exists():
            print(result.stderr[-4000:])
            if log.exists():
                print(log.read_text()[-6000:])
            raise SystemExit("FAIL: окно или ядро не запустились")
        state = json.loads(report.read_text())
        if "--report" in sys.argv:
            destination = Path(sys.argv[sys.argv.index("--report") + 1]).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        if not state.get("bridge") or not state.get("connected") or state.get("body", 0) < 100 or not state.get("studio"):
            raise SystemExit(f"FAIL: страница не загрузилась: {state}")
        if not state.get("text_mode") or not (state.get("mic_disabled") or state.get("mic_switch")):
            raise SystemExit(f"FAIL: text-only app exposes a microphone: {state}")
        if not state.get("mic_permission_ui") or state.get("mic_permission") not in ("not_determined", "authorized", "denied", "restricted"):
            raise SystemExit(f"FAIL: microphone permission status is unavailable: {state}")
        if not state.get("subscription_flow", {}).get("passed"):
            raise SystemExit(f"FAIL: subscription UI flow: {state}")
        if not state.get("subscription_ui"):
            raise SystemExit(f"FAIL: subscription model controls unavailable: {state}")
        if "--features" in sys.argv:
            expected = {"capture", "capture_saved", "capture_reopened", "pad", "pad_reopened", "prompter", "alarm", "zoom", "zoom_small"}
            if any(state.get(key) is not True for key in expected):
                raise SystemExit(f"FAIL: native feature windows: {state}")
        print("OK: native Cocoa window, existing HUD HTML, JS bridge, core startup and shutdown", state)


if __name__ == "__main__":
    main()
