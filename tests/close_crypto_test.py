"""«Близкие», шифрование: Python (core/closecrypto.py) и JS (ui/assets/closecrypto.js, телефон) считают одинаково —
векторы RFC 7748 (X25519) и RFC 4231 (HMAC-SHA256), конверт с одной стороны открывается другой, подмена ловится."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


import closecrypto as C  # noqa: E402
H = bytes.fromhex
V1 = ("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4", "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c",
      "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")
ok(C.x25519(H(V1[0]), H(V1[1])).hex() == V1[2], "X25519: вектор RFC 7748 (Python)")
A_PRIV = H("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
B_PRIV = H("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
SHARED = "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"
ok(C.x25519(A_PRIV, C.x25519(B_PRIV, C.BASE)).hex() == SHARED, "X25519: общий ключ Алисы и Боба (RFC 7748 §6.1)")
env = C.encrypt_for({"t": "Привет, мама 👋"}, A_PRIV, "devA", {"devB": C.x25519(B_PRIV, C.BASE), "devA2": C.x25519(A_PRIV, C.BASE)})
ok(C.decrypt_from(env, B_PRIV, "devB", C.x25519(A_PRIV, C.BASE)) == {"t": "Привет, мама 👋"}, "Python ↔ Python: конверт открылся у получателя")
bad = dict(env, c=env["c"][:-2] + ("A" if env["c"][-2] != "A" else "B") + env["c"][-1])
try:
    C.decrypt_from(bad, B_PRIV, "devB", C.x25519(A_PRIV, C.BASE)); ok(False, "подмена шифротекста не замечена")
except C.CloseCryptoError:
    ok(True, "подмена шифротекста — ошибка, а не мусор")
try:
    C.decrypt_from(env, H("11" * 32), "devB", C.x25519(A_PRIV, C.BASE)); ok(False, "чужой ключ открыл сообщение")
except C.CloseCryptoError:
    ok(True, "чужое устройство не открывает")
big = os.urandom(5 * 1024 * 1024); k = os.urandom(32)
t0 = time.time(); blob = C.seal(k, big); ok(C.unseal(k, blob) == big, f"файл 5 МБ: туда и обратно за {time.time() - t0:.1f} с")

if not shutil.which("node"):
    print("нет node — JS не проверяю")
else:
    js = (ROOT / "ui/assets/closecrypto.js").read_text(encoding="utf-8")
    pyenv = json.dumps(C.encrypt_for({"t": "из Python", "n": 5}, A_PRIV, "pc", {"phone": C.x25519(B_PRIV, C.BASE)}))
    script = js + r"""
const D = globalThis.DewiarCrypto, hx = (s) => Uint8Array.from(s.match(/../g).map(h => parseInt(h, 16))), xh = (b) => Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
const out = {};
out.rfc = xh(D.x25519(hx(V1[0]), hx(V1[1])));
out.sha = xh(D.sha256(D.utf8("abc")));
out.hmac = xh(D.hmac(D.utf8("Jefe"), D.utf8("what do ya want for nothing?")));
out.shared = xh(D.x25519(hx(A), D.x25519(hx(B), D.BASE)));
out.fromPy = D.decryptFrom(PYENV, hx(B), "phone", D.x25519(hx(A), D.BASE));
out.toPy = D.encryptFor({ t: "из телефона 📱" }, hx(B), "phone", { pc: D.x25519(hx(A), D.BASE) });
const big = D.randomBytes(2 * 1024 * 1024), k = D.randomBytes(32), t0 = Date.now(), blob = D.seal(k, big), back = D.unseal(k, blob);
out.big = back.length === big.length && back.every((x, i) => x === big[i]); out.bigMs = Date.now() - t0;
const kp = D.keypair(); out.kp = kp.pub.length === 32;
console.log(JSON.stringify(out));
""".replace("V1", json.dumps(V1)).replace("PYENV", pyenv).replace("hx(A)", f"hx('{A_PRIV.hex()}')").replace("hx(B)", f"hx('{B_PRIV.hex()}')")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=120)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        o = {}; print(r.stderr[-800:])
    ok(o.get("rfc") == V1[2] and o.get("shared") == SHARED, "X25519 в JS — те же векторы RFC 7748")
    ok(o.get("sha") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
       and o.get("hmac") == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843", "SHA-256 и HMAC в JS — векторы FIPS и RFC 4231")
    ok(o.get("fromPy") == {"t": "из Python", "n": 5}, "Python → JS: телефон открыл сообщение компьютера")
    ok(o.get("toPy") and C.decrypt_from(o["toPy"], A_PRIV, "pc", C.x25519(B_PRIV, C.BASE)) == {"t": "из телефона 📱"}, "JS → Python: компьютер открыл сообщение телефона")
    ok(o.get("big") and o.get("kp"), f"JS: файл 2 МБ туда и обратно за {o.get('bigMs')} мс, новая пара ключей")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
