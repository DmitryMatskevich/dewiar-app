"""Версию, которую окна читают из core/version.py регулярным выражением (ui/app.py _disk_version, ui/doctor.py),
должна совпадать с той, что даёт импорт. 0.28.3: пояснение в файле с образцом строки версии -> окно читало «…»,
считало, что пакет обновили, и перезапускалось по кругу каждые 20 с с уведомлением «Dewiar обновлён»."""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
from version import VERSION  # noqa: E402

bad = 0


def check(ok, msg):
    global bad
    print(("✅ " if ok else "❌ ") + msg)
    bad += not ok


src = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
for f in ("ui/app.py", "ui/doctor.py"):
    code = (ROOT / f).read_text(encoding="utf-8")
    m = re.search(r"re\.search\((r'[^']*VERSION[^']*'),", code)
    check(bool(m), f"{f}: чтение версии из файла найдено")
    if m:
        rx = eval(m.group(1))                                           # noqa: S307 — свой файл
        flags = re.M if "re.M" in code[m.end():m.end() + 200] else 0
        got = re.search(rx, src, flags)
        check(bool(got) and got.group(1) == VERSION, f"{f}: читает {got and got.group(1)!r}, импорт даёт {VERSION!r}")

# сам _disk_version из ui/app.py (без запуска окон: вырезаем функцию)
code = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
fn = re.search(r"\ndef _disk_version\(\):.*?(?=\n\n\n)", code, re.S).group(0)
ns = {"ROOT": ROOT}
exec(fn, ns)                                                            # noqa: S102
check(ns["_disk_version"]() == VERSION, f"_disk_version() = {ns['_disk_version']()!r}")

# предохранитель: не номер версии — не перезапускаемся; та же пара версий второй раз — не перезапускаемся
fn = re.search(r"\ndef watch_version\(api\):.*?(?=\n\n\n)", code, re.S).group(0)
with tempfile.TemporaryDirectory() as d:
    os.environ["DEWIAR_STATE_DIR"] = d
    import types
    calls = []
    for disk, expect, why in (("…", 0, "не номер версии — без перезапуска"),
                              ("9.9.9", 1, "новая версия — один перезапуск"),
                              ("9.9.9", 0, "та же пара версий второй раз — без перезапуска")):
        t = types.SimpleNamespace(n=0)

        def sleep(_s, t=t, disk=disk):
            t.n += 1
            if t.n > 4:
                raise StopIteration

        ns = {"UI_VERSION": VERSION, "_disk_version": lambda disk=disk: disk, "sys": sys,
              "time": types.SimpleNamespace(sleep=sleep)}
        exec(fn, ns)                                                    # noqa: S102
        api = types.SimpleNamespace(restart=lambda: calls.append(1))
        before = len(calls)
        try:
            ns["watch_version"](api)
        except StopIteration:
            pass
        check(len(calls) - before == expect, why)

print("ALL OK" if not bad else f"ошибок: {bad}")
sys.exit(1 if bad else 0)
