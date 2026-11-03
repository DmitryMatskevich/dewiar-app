#!/usr/bin/env python3
"""Проверка «Лира, вставить» без голоса: ⌘C и ⌘V в поле, где стоит курсор.

Запуск на Mac из каталога приложения:  python3 tools/paste_check.py
За 5 секунд щёлкните в любое текстовое поле (Заметки, TextEdit, поле в браузере) — туда вставится строка
«Проверка вставки Dewiar …». Разрешение «Универсальный доступ» при запуске из Терминала нужно Терминалу
(или приложению, из которого запущен скрипт), а не Dewiar.app.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import ApplicationServices as ax  # noqa: E402
import background_dictation as bd  # noqa: E402


def main():
    print("Универсальный доступ:", "есть" if ax.AXIsProcessTrusted() else "НЕТ — macOS не пропустит ⌘V")
    print("Окна спереди назад:", ", ".join(f"{name} ({pid})" for pid, name in bd._window_owners()[:6]) or "—")
    for left in range(5, 0, -1):
        print(f"Вставка через {left} с — щёлкните в текстовое поле…", flush=True)
        time.sleep(1)
    pid = bd._focused_pid(ax)
    print("Активное приложение:", pid or "не определено", "(собственные процессы Dewiar:", sorted(bd._dewiar_pids()), ")")
    result = bd.copy_and_insert("Проверка вставки Dewiar " + time.strftime("%H:%M:%S"))
    if result["inserted"]:
        print("Готово: ⌘V отправлено в «" + result["app"] + "». Если текста в поле нет — сообщите, в каком приложении.")
    else:
        print("Скопировано, но не вставлено:", result["reason"])
    return 0 if result["inserted"] else 1


if __name__ == "__main__":
    sys.exit(main())
