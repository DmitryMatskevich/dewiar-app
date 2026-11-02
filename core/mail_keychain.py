"""Пароли почты в Связке ключей macOS — без системных окон при запуске и в фоне.

Почему раньше при входе в Dewiar macOS просила пароль от Связки ключей. Запись «dewiar-mail» лежит в обычной
(файловой) Связке ключей, и macOS выдаёт пароль без вопросов только приложению из её списка доступа. Приложение
в списке опознаётся по подписи кода. Сборка Dewiar без постоянной подписи (ad hoc) после каждой пересборки — для
macOS новое приложение, поэтому первое же чтение пароля (значок почты при запуске) показывало окно
«Dewiar хочет использовать конфиденциальную информацию…».

Как это делает Apple Mail: подпись Apple не меняется, поэтому Mail всегда остаётся в списке доступа своих записей.
Пароль читается, только когда нужен вход на сервер. Если сервер пароль не принял, Mail сама просит ввести его
заново и после удачного входа обновляет запись.

Что делает этот модуль:
- фоновые операции (запуск, синхронизация, перенос старого пароля) идут с запретом окон: если macOS захочет
  что-то спросить, запрос сразу завершается кодом errSecInteractionNotAllowed, и ящик получает состояние
  «нужен доступ», а не блокирующее окно;
- наличие пароля проверяется по атрибутам записи: для этого macOS не нужно расшифровывать пароль и не нужно
  спрашивать пользователя;
- окно macOS допускается только по явному действию пользователя («Разрешить доступ к Связке ключей»);
- при записи запись, недоступная этой сборке, не перезаписывается через окно: создаётся новая запись этой же
  службы с другим ключом (account), ссылка на неё хранится в настройках ящика (без секрета).
"""

import logging
import sys
import threading
import time
from contextlib import contextmanager

log = logging.getLogger("dewiar.mail.keychain")
SERVICE = "dewiar-mail"

# Состояния секрета ящика (их же видит студия).
OK = "ok"                      # пароль прочитан
MISSING = "missing"            # записи нет
NEEDS_ACCESS = "needs_access"  # запись есть, но macOS спросила бы разрешение (новая сборка, отказ, «Запретить»)
LOCKED = "locked"              # Связка ключей заблокирована
UNAVAILABLE = "unavailable"    # не macOS, нет PyObjC или иная ошибка

# Коды Security.framework (SecBase.h).
ERR_SUCCESS = 0
ERR_USER_CANCELED = -128
ERR_AUTH_FAILED = -25293
ERR_NO_SUCH_KEYCHAIN = -25294
ERR_DUPLICATE_ITEM = -25299
ERR_ITEM_NOT_FOUND = -25300
ERR_NO_DEFAULT_KEYCHAIN = -25307
ERR_INTERACTION_NOT_ALLOWED = -25308
ERR_INTERACTION_REQUIRED = -25315
ERR_MISSING_ENTITLEMENT = -34018
_ACCESS_CODES = {ERR_INTERACTION_NOT_ALLOWED, ERR_INTERACTION_REQUIRED, ERR_AUTH_FAILED, ERR_USER_CANCELED}

# Запрет окон в Security.framework действует на весь процесс; все операции со Связкой ключей — по очереди.
_lock = threading.RLock()


class KeychainError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _security():
    if sys.platform != "darwin":
        return None
    try:
        import Security
        return Security
    except ImportError:
        return None


def available():
    return _security() is not None


def _split(result):
    """PyObjC возвращает код или кортеж (код, значение)."""
    if isinstance(result, tuple):
        return int(result[0]), (result[1] if len(result) > 1 else None)
    return int(result), None


@contextmanager
def _ui(S, interactive):
    """Внутри блока macOS не показывает окна Связки ключей (если interactive=False). Отдаёт, удалось ли это гарантировать."""
    with _lock:
        if interactive:
            yield True
            return
        setter = getattr(S, "SecKeychainSetUserInteractionAllowed", None)
        getter = getattr(S, "SecKeychainGetUserInteractionAllowed", None)
        previous = True
        if getter:
            try:
                status, value = _split(getter(None))
                if status == ERR_SUCCESS and value is not None:
                    previous = bool(value)
            except Exception:  # noqa: BLE001
                pass
        guarded = False
        if setter:
            try:
                guarded = _split(setter(False))[0] == ERR_SUCCESS
            except Exception:  # noqa: BLE001
                guarded = False
        try:
            yield guarded
        finally:
            if guarded:
                try:
                    setter(previous)
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось вернуть разрешение окон Связки ключей")


def _query(S, account, service, interactive):
    q = {S.kSecClass: S.kSecClassGenericPassword, S.kSecAttrService: service, S.kSecAttrAccount: str(account)}
    if not interactive:
        fail = getattr(S, "kSecUseAuthenticationUIFail", None)
        key = getattr(S, "kSecUseAuthenticationUI", None)
        if key is not None and fail is not None:
            q[key] = fail
    return q


def _state_of(status):
    if status == ERR_SUCCESS:
        return OK
    if status == ERR_ITEM_NOT_FOUND:
        return MISSING
    if status in _ACCESS_CODES:
        return NEEDS_ACCESS
    if status in (ERR_NO_SUCH_KEYCHAIN, ERR_NO_DEFAULT_KEYCHAIN):
        return LOCKED
    return UNAVAILABLE


def exists(account, service=SERVICE):
    """Есть ли запись — по атрибутам, без расшифровки пароля и без окон -> OK | MISSING | LOCKED | UNAVAILABLE."""
    S = _security()
    if S is None or not account:
        return UNAVAILABLE if S is None else MISSING
    try:
        with _ui(S, False):
            q = _query(S, account, service, False)
            q.update({S.kSecReturnAttributes: True, S.kSecMatchLimit: S.kSecMatchLimitOne})
            status, _ = _split(S.SecItemCopyMatching(q, None))
    except Exception:  # noqa: BLE001
        log.exception("Связка ключей: не удалось проверить запись %s", account)
        return UNAVAILABLE
    state = _state_of(status)
    return LOCKED if state == NEEDS_ACCESS else state


def read(account, service=SERVICE, interactive=False):
    """Прочитать пароль -> (состояние, пароль или ""). Без interactive окно macOS не появляется никогда:
    если гарантировать это нельзя (старая PyObjC без SecKeychainSetUserInteractionAllowed), пароль не читается."""
    S = _security()
    if S is None or not account:
        return (UNAVAILABLE if S is None else MISSING), ""
    try:
        with _ui(S, interactive) as guarded:
            if not guarded:
                found = exists(account, service)
                return (NEEDS_ACCESS if found == OK else found), ""
            q = _query(S, account, service, interactive)
            q.update({S.kSecReturnData: True, S.kSecMatchLimit: S.kSecMatchLimitOne})
            status, value = _split(S.SecItemCopyMatching(q, None))
    except Exception:  # noqa: BLE001
        log.exception("Связка ключей: не удалось прочитать запись %s", account)
        return UNAVAILABLE, ""
    if status == ERR_SUCCESS:
        try:
            return OK, bytes(value).decode("utf-8")
        except (TypeError, ValueError):
            return UNAVAILABLE, ""
    state = _state_of(status)
    if state == NEEDS_ACCESS and not interactive and exists(account, service) != OK:
        state = LOCKED          # атрибуты тоже не читаются — заблокирована сама Связка ключей
    if state == UNAVAILABLE:
        log.warning("Связка ключей: чтение %s завершилось кодом %s", account, status)
    return state, ""


def _attributes(S, secret, label):
    a = {S.kSecValueData: str(secret).encode("utf-8")}
    if label:
        a[S.kSecAttrLabel] = label
    return a


def write(account, secret, service=SERVICE, label=""):
    """Сохранить пароль без окон -> ключ записи (account), под которым он лежит.

    Своя запись обновляется на месте. Запись, недоступная этой сборке, не трогается: рядом создаётся новая
    (account#время) — её ключ возвращается и хранится в настройках ящика."""
    S = _security()
    if S is None:
        raise KeychainError("Связка ключей macOS недоступна (нужен macOS и компонент PyObjC Security).")
    if not account:
        raise KeychainError("Не указан адрес ящика для записи в Связку ключей.")
    with _ui(S, False):
        base = _query(S, account, service, False)
        status, _ = _split(S.SecItemAdd({**base, **_attributes(S, secret, label)}, None))
        if status == ERR_SUCCESS:
            return account
        if status == ERR_DUPLICATE_ITEM:
            status, _ = _split(S.SecItemUpdate(base, _attributes(S, secret, "")))
            if status == ERR_SUCCESS:
                return account
            if status in _ACCESS_CODES and _split(S.SecItemDelete(base))[0] == ERR_SUCCESS:
                status, _ = _split(S.SecItemAdd({**base, **_attributes(S, secret, label)}, None))
                if status == ERR_SUCCESS:
                    return account
        if status not in _ACCESS_CODES:
            raise KeychainError(f"macOS не сохранила пароль почты в Связку ключей (код {status}).", status)
        root = str(account).split("#", 1)[0]
        fresh = f"{root}#{int(time.time())}"
        status, _ = _split(S.SecItemAdd({**_query(S, fresh, service, False), **_attributes(S, secret, label)}, None))
        if status != ERR_SUCCESS:
            raise KeychainError(f"macOS не сохранила пароль почты в Связку ключей (код {status}).", status)
        log.info("Связка ключей: прежняя запись %s недоступна этой сборке Dewiar — пароль сохранён в новую запись", account)
        return fresh


def delete(account, service=SERVICE):
    """Удалить запись без окон -> True, если удалена или её нет. Чужую для этой сборки запись macOS удалить не даст."""
    S = _security()
    if S is None or not account:
        return S is None
    try:
        with _ui(S, False):
            status, _ = _split(S.SecItemDelete(_query(S, account, service, False)))
    except Exception:  # noqa: BLE001
        log.exception("Связка ключей: не удалось удалить запись %s", account)
        return False
    if status not in (ERR_SUCCESS, ERR_ITEM_NOT_FOUND):
        log.info("Связка ключей: запись %s не удалена (код %s); её можно удалить в «Связке ключей» вручную", account, status)
    return status in (ERR_SUCCESS, ERR_ITEM_NOT_FOUND)
