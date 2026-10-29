"""Проверка в конце установки на Windows (её запускает установщик): программа целая, библиотеки загружаются.
Что-то мешает (обычно антивирус убрал файлы в карантин) — объясняет окном, что сделать; если охраняет Защитник
Windows — предлагает добавить Dewiar в его исключения (Windows спросит разрешение).
Распознавание карантина, тексты объяснений и исключения Защитника — в core/winav.py (им же пользуется ui/doctor.py).
Код выхода: 0 — всё хорошо, 2 — что-то не так (с --quiet окон нет, иначе человеку уже объяснили)."""
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "core"))
import winav  # noqa: E402

MODS = ["numpy", "onnxruntime", "vosk", "sounddevice", "webview", "websockets"]


def main():
    errs = []
    for m in MODS:
        try:
            __import__(m)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{m}: {type(e).__name__}: {e}")
    miss = winav.missing(APP)
    if not errs and not miss:
        print("ok")
        return 0
    print("\n".join(errs + miss))
    if "--quiet" in sys.argv:
        return 2
    import ctypes
    # MessageBoxW: 0x40000 — поверх всех окон, 0x10000 — на передний план (иначе окно прячется за установщиком);
    # 0x40 — значок «информация», 0x30 — «внимание», 0x4 — кнопки «Да/Нет», ответ 6 — «Да»
    box = lambda text, flags=0x40: ctypes.windll.user32.MessageBoxW(None, text, "Установка Dewiar", flags | 0x40000 | 0x10000)  # noqa: E731
    st = winav.status(APP)
    if winav.looks_blocked(" ".join(errs), miss):
        winav.copy_text(st["paths"][0])
        text = winav.message(st, errs[0] if errs else "")
        if (st.get("main") or {}).get("kind") == "defender":
            if box(text + "\n\nОткрыть настройки Защитника Windows, чтобы добавить папку Dewiar в исключения?", 0x4 | 0x30) == 6:
                winav.defender_exclude(st["paths"])
                box("В открывшемся окне: «Исключения» → «Добавить исключение» → «Папка» → Ctrl+V (путь уже скопирован).\n\n"
                    "Потом запустите установщик Dewiar ещё раз: он вернёт убранные файлы.")
        else:
            box(text + "\n\nПотом запустите установщик Dewiar ещё раз — он вернёт убранные файлы.", 0x30)
    else:
        box("Не все библиотеки Dewiar загружаются:\n\n" + "\n".join(errs[:4]) +
            "\n\nЗапустите установщик ещё раз. Не поможет — меню «Пуск» → «Dewiar — диагностика» и пришлите отчёт разработчику.", 0x30)
    return 2


if __name__ == "__main__":
    sys.exit(main())
