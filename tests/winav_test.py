"""Антивирус (Windows) и сбой запуска ядра: какие файлы пропали, понятные шаги для Kaspersky/ESET/Dr.Web/Защитника,
признаки «антивирус мешает» в ошибке; ядро, упавшее при запуске, записывает причину в core.fail (её читает сторож
интерфейса и диагностика). На Linux — только то, что от Windows не зависит."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
sys.path.insert(0, str(ROOT / "core"))
import winav as W  # noqa: E402

tmp = Path(tempfile.mkdtemp())
prog = tmp / "Programs" / "Dewiar"
for f in ("python/python.exe", "python/python312.dll", "app/core/main.py", "app/ui/app.py",
          "python/Lib/site-packages/onnxruntime/__init__.py", "python/Lib/site-packages/vosk/libvosk.dll"):
    (prog / f).parent.mkdir(parents=True, exist_ok=True); (prog / f).write_text("x")
miss = W.missing(prog / "app")
ok("python/pythonw.exe" in miss and any("onnxruntime" in m for m in miss) and not any("vosk" in m for m in miss),
   f"пропавшие файлы видны: {miss}")
ok(W.missing(ROOT) == [], "запуск из исходников — проверять нечего")
ok(W.kind_of("Kaspersky Standard") == "kaspersky" and W.kind_of("Windows Defender") == "defender" and W.kind_of("ESET Security") == "eset"
   and W.kind_of("Dr.Web Security Space") == "drweb" and W.kind_of("Что-то своё") == "other", "антивирус узнаётся по названию")
ok(W.main_av([{"name": "Windows Defender", "kind": "defender", "on": False}, {"name": "Kaspersky", "kind": "kaspersky", "on": True}])["kind"] == "kaspersky"
   if os.name == "nt" else True, "сторонний включённый антивирус важнее Защитника")
paths = W.install_paths(prog / "app")
ok(paths[0] == str(prog), f"папка программы: {paths[0]}")
k = W.advice("kaspersky", paths)
ok(any("Исключения" in s for s in k["steps"]) and any("Ctrl+V" in s for s in k["steps"]) and str(prog) in " ".join(k["steps"]) and not k["auto"],
   "Kaspersky: шаги «Исключения», путь уже в буфере (Ctrl+V)")
d = W.advice("defender", paths)
ok(d["auto"] and "Открыть настройки Защитника" in d["steps"][0] and "Ctrl+V" in d["steps"][1],
   "Защитник: кнопка открывает его настройки, путь уже скопирован (сами исключения не добавляем — так делают вирусы)")
src = (ROOT / "core/winav.py").read_text(encoding="utf-8") + (ROOT / "packaging/windows/dewiar.nsi").read_text(encoding="utf-8")
ok("ShellExecuteW(None, \"runas\", \"powershell.exe\"" not in src and "Add-MpPreference" not in src,
   "ни в программе, ни в установщике нет скрытого PowerShell с Add-MpPreference (Kaspersky: PDM:Trojan.Win32.Generic)")
ok(W.looks_blocked("ImportError: DLL load failed while importing onnxruntime_pybind11_state", []) and W.looks_blocked("", ["python/pythonw.exe"])
   and W.looks_blocked("PermissionError: [WinError 5] Отказано в доступе", []) and not W.looks_blocked("ValueError: bad settings", []),
   "признаки «мешает антивирус» в ошибке")
msg = W.message({"advice": k, "missing": miss, "paths": paths}, "DLL load failed")
ok("ложная тревога" in msg and "1. " in msg and "Ошибка: DLL load failed" in msg, "сообщение человеку: что случилось и шаги")
if os.name != "nt":
    ok(W.status(ROOT) == {"supported": False} and W.products() == [] and not W.defender_exclude(paths), "не Windows — карточки антивируса нет")

# ядро, упавшее при запуске, оставляет причину
st = tmp / "state"
env = dict(os.environ, DEWIAR_STATE_DIR=str(st), DEWIAR_CONFIG_DIR=str(tmp / "cfg"))
code = ("import sys, runpy; sys.modules['bridge'] = None; sys.argv = ['main.py', '--log', r'%s'];"
        "runpy.run_path(r'%s', run_name='__main__')") % (tmp / "core.log", ROOT / "core" / "main.py")
r = subprocess.run([PY.replace("pythonw.exe", "python.exe"), "-X", "utf8", "-c", code], env=env, capture_output=True, text=True, timeout=120)
try:
    fail = json.loads((st / "core.fail").read_text(encoding="utf-8"))
except (OSError, ValueError):
    fail = {}
ok(r.returncode != 0 and "bridge" in fail.get("error", "") and fail.get("trace"), f"ядро не запустилось — причина в core.fail: {fail.get('error', '')[:80]}")
log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace") if (tmp / "core.log").exists() else ""
ok("Ядро не запустилось" in log, "и в журнале ядра")
