"""Антивирус на Windows: какой стоит, не убрал ли он файлы Dewiar, как добавить Dewiar в исключения.

Антивирусы иногда принимают Python-программы за подозрительные («ложная тревога») и молча убирают файлы в карантин —
человек видит только «файл не найден» или «нет связи с ядром». Здесь: список антивирусов (Центр безопасности
Windows), проверка ключевых файлов, понятные шаги для Kaspersky, ESET, Dr.Web, Avast/AVG и других, а для Защитника
Windows — кнопка открывает его настройки исключений (путь уже в буфере обмена). Сами исключения Dewiar не добавляет:
скрытый PowerShell, добавляющий исключения Защитника — ровно то, что делают вирусы, и Kaspersky за это и убирал Dewiar (комментарии
к ролику 20.10: «Касперский удаляет ещё на стадии установки», PDM:Trojan.Win32.Generic).
Только стандартная библиотека: работает и из установщика, и из диагностики, когда сам Dewiar не запускается.
"""

import json
import os
import re
import subprocess
from pathlib import Path

WIN = os.name == "nt"
NO_WINDOW = 0x08000000           # CREATE_NO_WINDOW: powershell без мелькающего окна консоли

# Порядок важен: kind_of берёт первое совпадение, поэтому «defender» — последним («Bitdefender» тоже содержит defender)
KINDS = [("kaspersky", r"kaspersky|касперск"), ("eset", r"\beset\b|nod32"), ("drweb", r"dr\.?\s?web"),
         ("avast", r"avast"), ("avg", r"\bavg\b"), ("norton", r"norton|symantec"), ("mcafee", r"mcafee"),
         ("bitdefender", r"bitdefender"), ("360", r"\b360\b"), ("defender", r"defender|защитник|windows security")]
TITLES = {"kaspersky": "Kaspersky", "eset": "ESET", "drweb": "Dr.Web", "avast": "Avast", "avg": "AVG", "norton": "Norton",
          "mcafee": "McAfee", "bitdefender": "Bitdefender", "360": "360 Total Security", "defender": "Защитник Windows"}
# Ошибки, по которым видна рука антивируса: «отказано в доступе» (WinError 5), «файл содержит вирус» (225),
# «заблокировано политикой» (1260), DLL не загрузилась, исчез .pyd/.dll/.exe (WinError 2)
AV_ERR = re.compile(r"WinError (5|225|1260|2)\b|Access is denied|Отказано в доступе|contains a virus|содержит вирус|"
                    r"DLL load failed|не удается найти указанный модуль|PermissionError|No such file.*\.(pyd|dll|exe)", re.I)


def kind_of(name):
    """Название антивируса -> ключ из KINDS («kaspersky», «defender»…) или «other»."""
    n =(name or "").lower()
    return next((k for k, rx in KINDS if re.search(rx, n)), "other")


def products(timeout=20):
    """[{"name", "kind", "on"}] — антивирусы из Центра безопасности Windows (на Windows Server и не-Windows — [])."""
    if not WIN:
        return []
    cmd = ("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue | "
           "ForEach-Object { $_.displayName + '|' + $_.productState }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + cmd],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    out, seen = [], set()
    for line in (r.stdout or "").splitlines():
        if "|" not in line:
            continue
        name, st = line.rsplit("|", 1)
        name = name.strip()
        try:
            on = ((int(st.strip()) >> 8) & 0xFF) in (0x10, 0x11)       # средний байт productState: 0x10/0x11 — включён
        except ValueError:
            on = True
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "kind": kind_of(name), "on": on})
    return out


def main_av(prods):
    """Кто на самом деле охраняет: включённый сторонний антивирус важнее Защитника (тот при нём отключается).
    Список пуст (Windows Server, сбой запроса) — считаем, что Защитник; не Windows — None."""
    on = [p for p in prods if p["on"]] or prods
    other = [p for p in on if p["kind"] != "defender"]
    return (other or on or [{"name": "Защитник Windows", "kind": "defender", "on": True}])[0] if WIN else None


def install_paths(root):
    """Папки Dewiar для исключений: программа (…\\Programs\\Dewiar; root — её подпапка app, рядом python) и данные —
    модели и журналы (%LOCALAPPDATA%\\Dewiar). Запуск из исходников — сама папка root."""
    root = Path(root)
    prog = root.parent if (root.parent / "python").exists() else root
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "Dewiar"
    return [str(prog), str(local)]


def missing(root):
    """Ключевые файлы установленной программы, которых нет (обычно — убраны антивирусом в карантин), — пути
    относительно папки программы. Запуск из исходников — []."""
    root = Path(root)
    prog = root.parent
    if not (prog / "python").exists():
        return []                                      # запуск из исходников — проверять нечего
    need = ["python/pythonw.exe", "python/python.exe", "app/core/main.py", "app/ui/app.py"]
    out = [p for p in need if not (prog / p).exists()]
    if not list((prog / "python").glob("python3*.dll")):
        out.append("python/python3*.dll")
    sp = prog / "python" / "Lib" / "site-packages"
    if sp.exists():
        for name, pattern in (("onnxruntime", "onnxruntime/capi/onnxruntime*.dll"), ("vosk", "vosk/libvosk.dll")):
            if (sp / name).exists() and not list(sp.glob(pattern)):
                out.append(f"…/site-packages/{pattern}")
    return out


def advice(kind, paths):
    """Что сделать человеку, словами -> {"title", "steps": [...], "auto": можно ли одной кнопкой, "path"}.
    Шаги для сторонних антивирусов рассчитаны на то, что путь уже в буфере обмена (copy_text)."""
    p = paths[0] if paths else "папка Dewiar"
    t = TITLES.get(kind, "антивирус")
    tail = ["Если файлы уже в карантине — восстановите их там же (раздел «Карантин») или просто запустите установщик Dewiar ещё раз.",
            "Это ложная тревога: Dewiar написан на Python и ничего не скрывает, а антивирусы иногда настороженно относятся к таким программам."]
    common = f"Путь к папке Dewiar уже скопирован — в нужном поле нажмите Ctrl+V. Путь: {p}"
    steps = {
        "defender": ["Нажмите «Открыть настройки Защитника» — откроется «Защита от вирусов и угроз» → «Управление настройками».",
                     "Прокрутите до «Исключения» → «Добавление или удаление исключений» → «Добавить исключение» → «Папка» → "
                     "вставьте путь (Ctrl+V) → «Выбор папки». Путь уже скопирован."],
        "kaspersky": ["Откройте Kaspersky (значок возле часов) → «Настройки» (шестерёнка) → «Параметры безопасности» → "
                      "«Исключения и действия при обнаружении объектов» (в некоторых версиях — «Угрозы и исключения») → "
                      "«Управление исключениями» → «Добавить».",
                      "В поле «Файл или папка» вставьте путь (Ctrl+V) → «Добавить».",
                      "Карантин: «Ещё инструменты» → «Карантин» → файлы Dewiar → «Восстановить»."],
        "eset": ["Откройте ESET → «Настройка» → «Расширенные параметры» (или F5) → «Обнаружение» → «Исключения» → "
                 "«Исключения производительности» → «Изменить» → «Добавить».", "Вставьте путь (Ctrl+V), добавьте в конце \\* → «ОК»."],
        "drweb": ["Нажмите на паука Dr.Web возле часов → замок «Разрешить изменения» → шестерёнка → «Исключения» → «Файлы и папки» → «+».",
                  "Вставьте путь (Ctrl+V) → «ОК»."],
        "avast": ["Откройте Avast → «Меню» → «Настройки» → «Общее» → «Исключения» → «Добавить исключение».", "Вставьте путь (Ctrl+V) → «Добавить исключение»."],
        "avg": ["Откройте AVG → «Меню» → «Настройки» → «Общее» → «Исключения» → «Добавить исключение».", "Вставьте путь (Ctrl+V) → «Добавить исключение»."],
    }.get(kind, [f"Откройте {t} и найдите раздел «Исключения» (иногда — «Доверенные программы», «Белый список» или «Разрешённые»).",
                 "Добавьте туда папку Dewiar: вставьте путь (Ctrl+V)."])
    return {"title": t, "steps": steps + ([] if kind == "defender" else [common]) + tail, "auto": kind == "defender", "path": p}


def status(root, with_products=True):
    """Сводка для диагностики и установщика: антивирусы, главный из них, пропавшие файлы, папки и совет.
    with_products=False — без запроса к PowerShell (он идёт до 20 с)."""
    if not WIN:
        return {"supported": False}
    prods = products() if with_products else []
    av = main_av(prods)
    paths = install_paths(root)
    return {"supported": True, "products": prods, "main": av, "missing": missing(root), "paths": paths,
            "advice": advice(av["kind"] if av else "defender", paths)}


def defender_exclude(paths):
    """Открыть настройки Защитника Windows («Защита от вирусов и угроз» → «Управление настройками», там «Исключения»)
    и положить путь к Dewiar в буфер обмена. Раньше — скрытый PowerShell, добавляющий исключения Защитника через runas: так ведут себя
    вирусы, и антивирусы (Kaspersky — PDM:Trojan.Win32.Generic) убирали из-за этого сам Dewiar. -> True, если окно открыто."""
    if not WIN:
        return False
    if paths:
        copy_text(str(paths[0]))
    try:
        os.startfile("windowsdefender://threatsettings")      # noqa: S606 — системная ссылка Windows, не файл
        return True
    except OSError:
        return False


def copy_text(text):
    """Положить текст в буфер обмена Windows (Ctrl+V вставит) -> удалось ли. Через WinAPI, без сторонних модулей;
    типы аргументов заданы явно — без них на 64-битной Windows указатели обрезаются до 32 бит."""
    if not WIN:
        return False
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GlobalAlloc.restype = wintypes.HGLOBAL
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u.OpenClipboard.argtypes = [wintypes.HWND]
    u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    data = (str(text) + "\0").encode("utf-16-le")
    if not u.OpenClipboard(None):
        return False
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(0x0002, len(data))                   # GMEM_MOVEABLE; после SetClipboardData память принадлежит системе
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        return bool(u.SetClipboardData(13, h))                 # CF_UNICODETEXT
    finally:
        u.CloseClipboard()


def looks_blocked(error_text, miss):
    """Похоже ли, что запуску мешает антивирус: пропали файлы или ошибка в духе AV_ERR."""
    return bool(miss) or bool(AV_ERR.search(error_text or ""))


def message(st, error=""):
    """Текст для окна-сообщения: что случилось и что сделать (по шагам)."""
    adv = st.get("advice") or advice("other", st.get("paths") or [])
    lines = []
    if st.get("missing"):
        lines.append("Похоже, антивирус убрал часть файлов Dewiar (это ложная тревога).")
    else:
        lines.append(f"Похоже, {adv['title']} не даёт Dewiar запуститься (это ложная тревога).")
    lines.append("")
    steps = list(adv["steps"])
    if adv.get("auto"):                                  # в окне «Да / Нет»: кнопка — это «Да»
        steps[0] = "Нажмите «Да» ниже — Dewiar добавит себя в исключения (Windows ещё раз спросит разрешение — тоже «Да»)."
    lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    if error:
        lines += ["", "Ошибка: " + error[:300]]
    return "\n".join(lines)


if __name__ == "__main__":                              # для проверки: python winav.py
    print(json.dumps(status(Path(__file__).resolve().parent.parent), ensure_ascii=False, indent=2))
