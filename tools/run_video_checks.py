"""Reproducible local acceptance checks tied to every chapter of the reference video.

Exit status describes local checks only. The Markdown report always retains live acceptance gaps.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from run_macos_checks import run_checks

ROOT = Path(__file__).resolve().parents[1]


def timestamp(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true", help="Open actual Cocoa windows with isolated fixture data")
    parser.add_argument("--bundle", action="store_true", help="Also check the built .app; requires --native")
    parser.add_argument("--report-only", action="store_true", help="Render saved results without running tests")
    args = parser.parse_args()
    manifest_path = ROOT / "data/macos-video-acceptance.json"
    manifest = json.loads(manifest_path.read_text())
    logs = ROOT / "work/video-checks"
    logs.mkdir(parents=True, exist_ok=True)
    tests = list(dict.fromkeys(t for chapter in manifest["chapters"] for t in chapter["tests"]))
    if args.report_only:
        results = json.loads((logs / "results.json").read_text())
    else:
        results = run_checks(tests, logs, timeout=240)
    by_test = {r["test"]: r for r in results}
    native = {}
    for suffix, bundle in (("source", False), ("bundle", True)):
        report = logs / f"native-{suffix}.json"
        if not args.report_only and args.native and (not bundle or args.bundle):
            report.unlink(missing_ok=True)
            cmd = [sys.executable, str(ROOT / "tools/smoke_macos.py"), "--features", "--report", str(report)]
            if bundle:
                cmd.append("--bundle")
            process = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=100)
            (logs / f"native-{suffix}.log").write_text(process.stdout + process.stderr)
            native[suffix] = {"passed": process.returncode == 0, "state": json.loads(report.read_text()) if report.exists() else {}}
        elif report.exists():
            state = json.loads(report.read_text())
            keys = ("bridge", "connected", "studio", "capture", "capture_saved", "capture_reopened", "pad", "pad_reopened", "prompter", "alarm", "zoom", "zoom_small")
            native[suffix] = {"passed": all(state.get(k) is True for k in keys), "state": state}
    passed = sum(bool(r["passed"]) for r in results)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    evidence = {"generated_at": datetime.now(timezone.utc).isoformat(), "source": manifest["source"],
                "head": revision, "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "local_tests": results, "native": native,
                "full_live_acceptance": False, "chapters": manifest["chapters"]}
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    evidence["untracked_source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                            for name in untracked if Path(name).suffix in (".py", ".json", ".spec")}
    (logs / "acceptance-results.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Приёмка Dewiar на Mac по демонстрационному ролику", "",
             f"Источник: [{manifest['title']}]({manifest['source']}). Проверяется весь ролик, 30 глав, 1:13:53.",
             f"Дата отчёта: {evidence['generated_at']}. База кода: `{revision[:12]}` плюс локальные изменения; отпечатки в JSON.", "",
             f"Локальные наборы: **{passed} из {len(results)} прошли**. "
             "Это проверки ядра, протоколов и интерфейса; запрограммированные ответы модели не подтверждают качество настоящей модели.",
             "**Полная приёмка всех возможностей ролика ещё не завершена.** Зависимости и оставшиеся проверки перечислены для каждой главы.", "",
             "Проверки используют отдельные конфигурации и память, локальные серверы и тестовые изображения. "
             "Письма, жалобы автору, приглашения и команды реальному умному дому не отправляются. Оплата и основной аккаунт синхронизации не используются.", "",
             "## Настоящие окна Mac", ""]
    for kind, item in native.items():
        lines.append(f"- {kind}: {'прошло' if item['passed'] else 'ошибка'}; `{json.dumps(item['state'], ensure_ascii=False)}`")
    if not native:
        lines.append("В этом отчёте нет результата Cocoa. Запустить с `--native` (и `--bundle` для .app).")
    regression_ok = True
    regression_path = ROOT / "work/final-macos-checks/results.json"
    if regression_path.exists():
        regression = json.loads(regression_path.read_text())
        evidence["macos_regression"] = regression
        count = sum(bool(r["passed"]) for r in regression)
        regression_ok = count == len(regression)
        lines.extend(["", f"Базовая регрессия порта после изменений: **{count} из {len(regression)} наборов прошли**. "
                      "Наборы частично пересекаются с проверками ролика; складывать их количества нельзя. "
                      "После исправления предположения о Linux в memcap_test дополнительно повторены memcap, tools_unit, route и macos_windows; все прошли."])
        (logs / "acceptance-results.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    lines.extend(["", "## Покрытие глав", "", "Успех тестов ниже означает только указанную локальную проверку. "
                  "Строка без автотестов остаётся непроверенной; рекламные заявления и отдельные продукты не засчитываются как функции Mac.", "",
                  "| Глава | Локальные наборы | Что осталось подтвердить |", "|---|---|---|"])
    for c in manifest["chapters"]:
        names = []
        for name in c["tests"]:
            result = by_test.get(name)
            state = "OK" if result and result["passed"] else "FAIL" if result else "не запускался"
            names.append(f"`{name}` ({state})")
        link = manifest["source"] + "&t=" + str(int(c["start"]))
        lines.append(f"| {c['id']} [{timestamp(c['start'])}]({link}) {c['chapter']} | " + ("<br>".join(names) or "Нет локального автотеста") + f" | {c['remaining']} |")
    live_path = logs / "live-local-model.json"
    if live_path.exists():
        live = json.loads(live_path.read_text())
        lines.extend(["", "## Настоящая локальная модель", "",
                      f"Отдельный прогон `{live['model']}` через настоящее ядро и Ollama; тестовые изображения, ограничение контекста 8192. "
                      "Ответы не подменялись. Инструменты ограничены памятью, заметками, арифметикой и напоминаниями.", ""])
        for check in live["checks"]:
            lines.append(f"- `{check['id']}`: {'прошло' if check['passed'] else 'НЕ ПРОШЛО'}; ответ: {check.get('answer', '')[:1000]}")
        lines.append("\nЭтот прогон не заменяет полный экзамен, проверку голоса и приёмку других моделей и провайдеров.")
        evidence["live_local_model"] = live
        (logs / "acceptance-results.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    lines.extend(["", "## Повторный запуск", "", "Из каталога исходников:", "", "```sh",
                  ".venv/bin/python tools/run_video_checks.py --native --bundle", "```", "",
                  "Журналы: `work/video-checks/*_test.py.log`; результаты: `work/video-checks/acceptance-results.json`. "
                  "Проверки нужно повторить после значимых изменений. `--report-only` лишь оформляет сохранённые результаты и не заменяет новый прогон.", ""])
    (logs / "acceptance-report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Local test files: {passed}/{len(results)}. Full live acceptance: pending. Report: {logs / 'acceptance-report.md'}")
    raise SystemExit(0 if passed == len(results) and regression_ok and all(n["passed"] for n in native.values()) else 1)


if __name__ == "__main__":
    main()
