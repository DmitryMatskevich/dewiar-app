"""Сквозное шифрование «Близких» (сообщения и файлы между помощниками разных людей) — только стандартный Python,
как crypt.py: сервер dewiar.pro хранит и пересылает лишь шифротекст.

Тот же формат — в ui/assets/closecrypto.js (телефон); tests/close_crypto_test.py сверяет обе стороны и векторы RFC.
  • ключи устройств — X25519 (RFC 7748), открытый ключ — 32 байта, в base64url без «=»;
  • seal(K, data) — K 32 байта: nonce 16 ‖ шифротекст ‖ тег 32. Поток ключа — HMAC-SHA256(Kenc, nonce ‖ номер блока, 4 байта),
    по 32 байта на блок; тег — HMAC-SHA256(Kmac, nonce ‖ шифротекст) (encrypt-then-MAC); Kenc/Kmac — HMAC(K, "dewiar-enc"/"-mac");
  • сообщение: случайный ключ K шифрует содержимое, а K «заворачивается» для каждого устройства получателя (и других устройств
    отправителя): W = HMAC(X25519(свой закрытый, его открытый), "dewiar-close-v1|<от>|<кому>"), обёртка = seal(W, K).
Формат лежит на сервере у людей: менять — только с новой версией v в конверте."""

import base64
import hashlib
import hmac
import json
import os

P = 2 ** 255 - 19
A24 = 121665
VERSION = 1


class CloseCryptoError(Exception):
    pass


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def x25519(k: bytes, u: bytes) -> bytes:
    """Скалярное умножение на кривой Curve25519 (лестница Монтгомери, RFC 7748 §5)."""
    if len(k) != 32 or len(u) != 32:
        raise CloseCryptoError("ключ X25519 — 32 байта")
    kb = bytearray(k)
    kb[0] &= 248
    kb[31] &= 127
    kb[31] |= 64
    n = int.from_bytes(kb, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        kt = (n >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = (da + cb) * (da + cb) % P
        z3 = x1 * (da - cb) * (da - cb) % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


BASE = (9).to_bytes(32, "little")


def keypair():
    """-> (закрытый, открытый) — по 32 байта."""
    priv = os.urandom(32)
    return priv, x25519(priv, BASE)


def _h(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def _stream(kenc: bytes, nonce: bytes, n: int) -> bytes:
    base = hmac.new(kenc, digestmod=hashlib.sha256)
    out = bytearray()
    for i in range((n + 31) // 32):
        h = base.copy()
        h.update(nonce + i.to_bytes(4, "big"))
        out += h.digest()
    return bytes(out[:n])


def _xor(a: bytes, b: bytes) -> bytes:
    if not a:
        return b""
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")


def seal(key: bytes, data: bytes, nonce: bytes = None) -> bytes:
    if len(key) != 32:
        raise CloseCryptoError("ключ — 32 байта")
    nonce = nonce or os.urandom(16)
    kenc, kmac = _h(key, b"dewiar-enc"), _h(key, b"dewiar-mac")
    ct = _xor(data, _stream(kenc, nonce, len(data)))
    return nonce + ct + _h(kmac, nonce + ct)


def unseal(key: bytes, blob: bytes) -> bytes:
    if len(key) != 32 or len(blob) < 48:
        raise CloseCryptoError("повреждённые данные")
    nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
    kenc, kmac = _h(key, b"dewiar-enc"), _h(key, b"dewiar-mac")
    if not hmac.compare_digest(tag, _h(kmac, nonce + ct)):
        raise CloseCryptoError("не сходится подпись — данные изменены или ключ не тот")
    return _xor(ct, _stream(kenc, nonce, len(ct)))


def wrap_key(priv: bytes, their_pub: bytes, from_dev: str, to_dev: str) -> bytes:
    s = x25519(priv, their_pub)
    if not any(s):
        raise CloseCryptoError("плохой открытый ключ")
    return _h(s, f"dewiar-close-v1|{from_dev}|{to_dev}".encode())


def encrypt_for(payload: dict, priv: bytes, my_dev: str, targets: dict) -> dict:
    """payload — JSON-словарь; targets — {id устройства: открытый ключ (bytes)}. -> конверт для сервера."""
    k = os.urandom(32)
    keys = {d: b64e(seal(wrap_key(priv, pub, my_dev, d), k)) for d, pub in targets.items()}
    return {"v": VERSION, "fd": my_dev, "k": keys,
            "c": b64e(seal(k, json.dumps(payload, ensure_ascii=False).encode("utf-8")))}


def decrypt_from(env: dict, priv: bytes, my_dev: str, sender_pub: bytes) -> dict:
    if int(env.get("v") or 0) != VERSION:
        raise CloseCryptoError("сообщение новой версии — обновите Dewiar")
    w = (env.get("k") or {}).get(my_dev)
    if not w:
        raise CloseCryptoError("сообщение не для этого устройства")
    k = unseal(wrap_key(priv, sender_pub, env.get("fd", ""), my_dev), b64d(w))
    return json.loads(unseal(k, b64d(env.get("c", ""))).decode("utf-8"))
