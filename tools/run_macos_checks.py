"""Последовательные проверки порта; старые тесты с ❌ и кодом выхода 0 считаются провалом.

Каждый набор получает отдельные конфигурацию, память и журналы. GUI-проверка запускается отдельно.
"""
import json
import os
import subprocess
import signal
import sys
import tempfile
import time
from pathlib import Path

TESTS = ["contacts_test.py", "contacts_flow_test.py", "context_help_test.py", "mac_control_test.py", "mac_control_flow_test.py", "strata_test.py", "strata_runtime_test.py", "report_delivery_test.py", "report_delivery_flow_test.py", "birthday_test.py", "birthday_flow_test.py", "code_safety_test.py", "macos_platform_test.py", "tasks_test.py", "issues1024_test.py",
         "long_task_test.py", "boundaries_test.py", "knowledge_test.py", "diary_test.py",
         "backup_test.py", "graph_test.py", "export_test.py", "close_crypto_test.py",
         "exam_isolation_test.py", "task_guard_test.py", "workspace_test.py", "memcap_test.py",
         "memory_pressure_test.py", "macos_chat_test.py", "sync_safety_test.py", "obsidian_test.py",
         "pad1020_test.py", "task_remove1024_test.py", "security1019_test.py", "macos_backup_test.py",
         "macos_windows_test.py", "video_condition_test.py", "text_mode_test.py", "model_download_test.py",
         "macos_mic_permission_test.py", "subscriptions_test.py", "voice_mode_test.py", "local_installations_test.py", "dewiar_services_test.py", "chat_archive_test.py", "model_router_test.py", "planning_council_test.py", "task_council_flow_test.py"]


def run_checks(names, logs, timeout=180):
    root = Path(__file__).resolve().parents[1]
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    for name in names:
        if Path(name).name != name or not name.endswith("_test.py") or not (root / "tests" / name).is_file():
            raise ValueError(f"Unknown test: {name}")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="dewiar-check-") as tmp:
            inherited = {key: value for key, value in os.environ.items() if not key.startswith("DEWIAR_")}
            env = dict(inherited, DEWIAR_CONFIG_DIR=tmp + "/config", DEWIAR_STATE_DIR=tmp + "/state",
                       DEWIAR_WORKSPACE_DIR=tmp + "/workspace", DEWIAR_NO_SCOPE="1",
                       DEWIAR_PROVISION=tmp + "/no-provision.json", PYTHONUNBUFFERED="1", DEWIAR_PY=sys.executable)
            code = None
            try:
                run = subprocess.Popen([sys.executable, str(root / "tests" / name)], env=env, cwd=root,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       start_new_session=True)
                try:
                    output, _ = run.communicate(timeout=timeout)
                    code = run.returncode
                    ok = code == 0 and "❌" not in output
                except subprocess.TimeoutExpired:
                    # Old tests spawn a core and fake servers. Stop only this owned process group.
                    os.killpg(run.pid, signal.SIGKILL)
                    output, _ = run.communicate(timeout=10)
                    output += "\nTIMEOUT"
                    ok = False
            except OSError as exc:
                output, ok = str(exc), False
            (logs / (name + ".log")).write_text(output, encoding="utf-8")
            results.append({"test": name, "passed": ok, "returncode": code,
                            "seconds": round(time.monotonic() - started, 2),
                            "checks": output.count("✅"), "failed_checks": output.count("❌")})
            print(("PASS " if ok else "FAIL ") + name, flush=True)
            if not ok:
                print(output[-2000:], flush=True)
    (logs / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def main():
    root = Path(__file__).resolve().parents[1]
    results = run_checks(sys.argv[1:] or TESTS, root / "work/checks")
    raise SystemExit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
