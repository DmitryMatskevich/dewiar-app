#!/usr/bin/env python3
"""Проверка паролей почты в Связке ключей без окон macOS. Пароли не выводятся.

Запуск на Mac из каталога приложения:  python3 tools/mail_keychain_check.py
Если во время проверки появилось окно Связки ключей — защита от окон не работает, сообщите об этом.
Состояние «нужен доступ» при запуске из Терминала — норма: запись принадлежит Dewiar.app, а не python3.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import mail_keychain as keychain  # noqa: E402
from settings import CONFIG_DIR  # noqa: E402

TITLES = {keychain.OK: "читается без окна", keychain.MISSING: "записи нет", keychain.NEEDS_ACCESS: "нужен доступ (окно не показано)",
          keychain.LOCKED: "Связка ключей заблокирована", keychain.UNAVAILABLE: "Связка ключей недоступна"}


def main():
    S = keychain._security()
    if S is None:
        print("Связка ключей недоступна: нужен macOS и пакет pyobjc-framework-Security.")
        return 1
    guard = hasattr(S, "SecKeychainSetUserInteractionAllowed")
    print("Запрет окон в фоне:", "есть" if guard else "НЕТ — Dewiar не будет читать пароли в фоне, только по кнопке")
    boxes = sorted((CONFIG_DIR / "mail-accounts").glob("*.json")) + [CONFIG_DIR / "mail.json"]
    for path in boxes:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ref = data.get("keychain_account") or data.get("address")
        if not ref:
            continue
        if data.get("password"):
            print(f"{path.name}: {data.get('address')} — пароль в файле (не в Связке ключей)")
            continue
        state, _ = keychain.read(ref)
        print(f"{path.name}: {data.get('address')} — запись «{ref}»: есть={keychain.exists(ref)}; чтение: {TITLES.get(state, state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
