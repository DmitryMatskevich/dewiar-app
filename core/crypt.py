"""Шифрование без сторонних библиотек (только стандартный Python): ключ из пароля — scrypt, шифр — поток SHAKE-256
(ключ ‖ nonce ‖ номер блока), целостность — HMAC-SHA256 (encrypt-then-MAC). Для сейфа и резервных копий.

seal(data, key)/unseal(blob, key)              — ключом 64 байта: «DWX1» ‖ nonce 16 ‖ HMAC 32 ‖ шифротекст;
seal_pw(data, password)/unseal_pw(blob, pw)   — паролем: «DWP1» ‖ соль 16 ‖ seal(…);
device_key(path)                               — ключ этого компьютера: Windows — защищён DPAPI, Linux — файл 0600.
Формат уже лежит в сейфах и копиях (.dwbak) пользователей: менять — только с новой MAGIC и чтением старой."""

import hashlib
import hmac
import os
import secrets

MAGIC, MAGIC_PW = b"DWX1", b"DWP1"
CHUNK = 1 << 20                                  # блок потока ключа: номер блока входит в SHAKE-256


class CryptError(Exception):
    pass


def _xor(data, enc, nonce):
    """data XOR поток SHAKE-256(ключ ‖ nonce ‖ номер блока) — одна функция и шифрует, и расшифровывает.
    nonce каждый раз новый: повтор nonce с тем же ключом раскрыл бы XOR двух текстов."""
    out = bytearray()
    for i in range(0, len(data), CHUNK):
        part = data[i:i + CHUNK]
        ks = hashlib.shake_256(enc + nonce + (i // CHUNK).to_bytes(8, "big")).digest(len(part))
        out += (int.from_bytes(part, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(part), "big")
    return bytes(out)


def seal(data, key):
    if len(key) != 64:
        raise CryptError("ключ — 64 байта")
    nonce = secrets.token_bytes(16)
    ct = _xor(data, key[:32], nonce)                 # первая половина ключа — шифр, вторая — HMAC
    mac = hmac.new(key[32:], MAGIC + nonce + ct, hashlib.sha256).digest()
    return MAGIC + nonce + mac + ct


def unseal(blob, key):
    if len(blob) < 52 or blob[:4] != MAGIC:
        raise CryptError("это не зашифрованные данные Dewiar")
    nonce, mac, ct = blob[4:20], blob[20:52], blob[52:]
    # сначала проверка HMAC (сравнение за постоянное время), расшифровка — только целых данных
    if not hmac.compare_digest(mac, hmac.new(key[32:], MAGIC + nonce + ct, hashlib.sha256).digest()):
        raise CryptError("неверный ключ или пароль, либо файл повреждён")
    return _xor(ct, key[:32], nonce)


def derive(password, salt):
    """Ключ 64 байта из пароля. scrypt n=2¹⁵, r=8 требует ~32 МБ памяти — больше стандартного предела OpenSSL,
    поэтому maxmem задан явно. Параметры менять нельзя: старые копии перестанут открываться."""
    return hashlib.scrypt(str(password).encode("utf-8"), salt=salt, n=1 << 15, r=8, p=1, dklen=64, maxmem=128 << 20)


def seal_pw(data, password):
    salt = secrets.token_bytes(16)
    return MAGIC_PW + salt + seal(data, derive(password, salt))


def unseal_pw(blob, password):
    if blob[:4] != MAGIC_PW:
        raise CryptError("файл не защищён паролем")
    return unseal(blob[20:], derive(password, blob[4:20]))


# ---------- ключ этого компьютера ----------

def _dpapi(data, protect):
    """Windows DPAPI (CryptProtectData/CryptUnprotectData) через ctypes: шифрует ключом учётной записи Windows.
    Результат выделяет Windows — освобождаем LocalFree."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    args = (ctypes.byref(src), "Dewiar", None, None, None, 0, ctypes.byref(dst)) if protect else \
        (ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst))
    if not fn(*args):
        raise CryptError("Windows не дал доступ к ключу сейфа")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def device_key(path, create=True):
    """Ключ этого компьютера, 64 байта. Windows: хранится зашифрованным DPAPI (расшифрует только этот пользователь
    Windows); Linux — как есть, файл с правами 0600. Нет файла: create=True — создаёт новый, иначе CryptError
    (новым ключом существующий сейф всё равно не открыть)."""
    from pathlib import Path
    p = Path(path)
    if p.exists():
        raw = p.read_bytes()
        return _dpapi(raw, False) if os.name == "nt" else raw
    if not create:
        raise CryptError("ключа сейфа нет")
    key = secrets.token_bytes(64)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    # права 600 — сразу при создании (os.open с mode), а не chmod после записи: иначе ключ мгновение читаем всем на машине
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(_dpapi(key, True) if os.name == "nt" else key)
    os.replace(tmp, p)
    return key
