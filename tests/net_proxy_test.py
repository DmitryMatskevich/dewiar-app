"""Прокси для серверов Dewiar — на случай, когда провайдер блокирует dewiar.pro.
Свой «dewiar.pro» по HTTPS (сертификат от тестового центра — как настоящий), SOCKS5 с логином и HTTP-прокси с CONNECT.
Через прокси идут только адреса Dewiar (dewiar.pro, поддомены, свой сайт), всё остальное — напрямую.
Сквозная проверка с ядром: без прокси вход не проходит и подсказывает, где прокси; ссылка t.me/socks сохраняется,
пароль прокси в ответах скрыт; нерабочий прокси не сохраняется; вход по https://dewiar.pro и обновления — через прокси."""
import asyncio, json, os, select, socket, ssl, struct, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
PY = os.environ.get("DEWIAR_PY") or sys.executable
TESTS = Path(__file__).resolve().parent
for k in [k for k in os.environ if k.lower().endswith("_proxy")]:
    os.environ.pop(k)
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)
tmp = Path(tempfile.mkdtemp())

sys.path.insert(0, str(TESTS))
import proxykit  # noqa: E402
K = proxykit.start(tmp)
os.environ["SSL_CERT_FILE"] = K.CA                                   # «системные» корневые сертификаты теста
SEEN, TLS, PLAIN, SP, HP, DEAD = K.SEEN, K.TLS, K.PLAIN, K.SP, K.HP, K.DEAD
SOCKS = f"socks5://anna:s3cret@127.0.0.1:{SP}"
URL = "https://dewiar.pro/dwrapp/latest.json"

# ---------------- модуль net в этом процессе ----------------
sys.path.insert(0, str(ROOT / "core"))
import httpx  # noqa: E402
import net  # noqa: E402
net.install()


def get(url, **kw):
    try:
        r = httpx.get(url, timeout=6, **kw)
        return r.status_code, r.text
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__


net.configure("")
n0 = len(SEEN["site"])
d = get(URL)
ok(len(SEEN["site"]) == n0 and SEEN["socks"] == 0, f"без прокси — напрямую (наш «сайт» за прокси не тронут): {d[0] or d[1]}")
net.configure(SOCKS)
d = get(URL)
ok(d[0] == 200 and '"0.0.1"' in d[1] and SEEN["socks"] == 1, f"https://dewiar.pro через SOCKS5 с логином: {d[0]}")
d = get("https://cdn.dewiar.pro/dwrapp/latest.json")
ok(d[0] == 200 and SEEN["socks"] == 2, "поддомен dewiar.pro — тоже через прокси")
with httpx.stream("GET", URL, timeout=6) as r:                       # как скачивают обновления и модели
    body = b"".join(r.iter_bytes())
ok(b"0.0.1" in body and SEEN["socks"] == 3, "скачивание потоком (обновления, модели) — через прокси")
s0 = SEEN["socks"]
d = get(f"http://127.0.0.1:{PLAIN}/dwrapp/latest.json")
ok(d[0] == 200 and SEEN["socks"] == s0, "другие адреса (свой сервер моделей, радио) — напрямую, мимо прокси")
d = get("https://my.site.test/dwrapp/latest.json")
ok(d[0] == 0 and SEEN["socks"] == s0, "чужой сайт без настройки — не через прокси")
net.configure(SOCKS, ["https://my.site.test/dwrapp/cloud.php"])
d = get("https://my.site.test/dwrapp/latest.json")
ok(d[0] == 200 and SEEN["socks"] == s0 + 1, "свой сайт (аккаунт/облако на своём домене) — через прокси")
net.configure(f"http://127.0.0.1:{HP}")
d = get(URL)
ok(d[0] == 200 and SEEN["http"] == 1, "HTTP-прокси (CONNECT) для https")
d = get("http://dewiar.pro/dwrapp/latest.json")
ok(d[0] == 200 and SEEN["http"] == 2, "HTTP-прокси для http")
net.configure("")
c = net.check(URL, SOCKS)
ok(not c["direct"][0] and c["proxy"][0] and "через прокси — работает" in c["advice"], f"проверка: {c['advice']}")
c = net.check(URL, f"socks5://127.0.0.1:{DEAD}")
ok(not c["proxy"][0] and "ни напрямую" in c["advice"], f"нерабочий прокси: {c['advice']}")
c = net.check(URL)
ok(not c["direct"][0] and "укажите прокси" in c["advice"], f"без прокси: {c['advice']}")
bad = []
for t in ("tg://proxy?server=1.2.3.4&port=443&secret=ee00", "ftp://x:21", "socks5://host"):
    try:
        net.normalize_proxy(t); bad.append(t)
    except net.NetError:
        pass
ok(not bad and net.normalize_proxy("10.0.0.5:3128") == "http://10.0.0.5:3128"
   and net.normalize_proxy(f"https://t.me/socks?server=127.0.0.1&port={SP}&user=anna&pass=s3cret") == SOCKS, "разбор адреса прокси")
# прокси «не с первого раза»: отвечает «502 Bad Gateway», пока не повторишь — Dewiar повторяет запрос сам
net.configure(f"http://127.0.0.1:{K.HPF}")
f0 = SEEN.get("http_502", 0)
d = get(URL)
ok(d[0] == 200 and SEEN.get("http_502", 0) - f0 == 2, f"прокси отвечает «502 Bad Gateway» дважды — Dewiar сам повторяет, третий раз проходит: {d[0] or d[1]}")
with httpx.stream("GET", URL, timeout=6) as r:
    body = b"".join(r.iter_bytes())
ok(b"0.0.1" in body, "и скачивание обновлений через такой прокси")
r_ = httpx.post("https://dewiar.pro/dwrapp/login.php", json={"login": "anna", "password": "pw"}, timeout=6)
ok(r_.status_code == 200 and r_.json().get("token") == "tok-123", "и вход (POST) — до сервера запрос не дошёл, повтор безопасен")
c = net.check(URL, f"http://127.0.0.1:{K.HPF}", timeout=4)
ok(c["proxy"][0], f"«Проверить» с таким прокси — работает с первого нажатия: {c['advice']}")
ok(net.hide(SOCKS) == f"socks5://anna:•••@127.0.0.1:{SP}", "пароль прокси для показа скрыт")
# прокси не пускает к серверу (молчит / 403 / обрыв TLS), а напрямую сервер доступен — идём напрямую и помним это
lz = net._Lazy(f"socks5://127.0.0.1:{DEAD}", True, None)
req = httpx.Request("GET", f"http://127.0.0.1:{PLAIN}/dwrapp/latest.json")
r_ = lz.handle_request(req); r_.read()
ok(r_.status_code == 200 and net._direct_ok.get("127.0.0.1", 0) > time.time(), "прокси не пускает, напрямую работает — ответ напрямую")
lz2 = net._Lazy(f"socks5://127.0.0.1:{DEAD}", True, None)
t0 = time.time(); r_ = lz2.handle_request(httpx.Request("GET", f"http://127.0.0.1:{PLAIN}/dwrapp/latest.json")); r_.read()
ok(r_.status_code == 200 and time.time() - t0 < 1 and lz2.t is None, "дальше — сразу напрямую, не дожидаясь прокси")
net._direct_ok.clear()

# ---------------- сквозная проверка с ядром ----------------
import websockets  # noqa: E402
port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
(tmp / "cfg").mkdir()
(tmp / "cfg/settings.json").write_text(json.dumps({"ui": {"welcome": "done"}, "stt": {"engine": "vosk"}, "tts": {"engine": "off"},
                                                    "updates": {"auto_check": False}}))
env = dict(os.environ, DEWIAR_CONFIG_DIR=str(tmp / "cfg"), DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_PORT=str(port), DEWIAR_ASSUME_AEC="1",
           DEWIAR_WORKSPACE=str(tmp / "ws"), DEWIAR_FAKE_RAM_GB="16", DEWIAR_FAKE_CORES="8", SSL_CERT_FILE=str(tmp / "ca.pem"))
core = subprocess.Popen([PY, str(ROOT / "core/main.py"), "--wav", str(TESTS / "silence90.wav"), "--null-audio"], env=env,
                        stdout=open(tmp / "core.log", "w"), stderr=subprocess.STDOUT)
R = {}


async def run():
    ws = None
    for _ in range(150):
        await asyncio.sleep(0.2)
        if (tmp / "cfg/token").exists():
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{port}", max_size=None); break
            except OSError:
                continue
    await ws.send(json.dumps({"cmd": "hello", "token": (tmp / "cfg/token").read_text().strip()}))
    n = [0]

    async def call(cmd, **args):
        n[0] += 1; i = n[0]
        await ws.send(json.dumps({"id": i, "cmd": cmd, "args": args}))
        while True:
            e = json.loads(await asyncio.wait_for(ws.recv(), 60))
            if e.get("type") == "reply" and e.get("id") == i:
                return e
    R["net0"] = (await call("net"))["data"]
    R["check0"] = (await call("net_check"))["data"]
    R["login0"] = await call("account_login", server="", login="anna", password="pw")
    R["bad"] = await call("net_proxy", proxy=f"socks5://127.0.0.1:{DEAD}")
    R["mt"] = await call("net_proxy", proxy="tg://proxy?server=1.2.3.4&port=443&secret=ee00")
    R["set"] = await call("net_proxy", proxy=f"tg://socks?server=127.0.0.1&port={SP}&user=anna&pass=s3cret")
    s0 = SEEN["socks"]
    R["login1"] = await call("account_login", server="", login="anna", password="pw")
    R["upd"] = (await call("update_check"))["data"]
    R["via"] = SEEN["socks"] - s0
    R["keep"] = await call("net_proxy", proxy=R["set"]["data"]["proxy"])          # как пришло в студию (со скрытым паролем)
    R["cfg_keep"] = json.loads((tmp / "cfg/settings.json").read_text())["net"]["proxy"]
    R["off"] = (await call("net_proxy", proxy=""))["data"]
    await ws.close()

try:
    asyncio.run(run())
finally:
    core.terminate(); core.wait(10)
log = (tmp / "core.log").read_text(encoding="utf-8", errors="replace")
ok(R["net0"]["on"] is False and "dewiar.pro" in R["net0"]["hosts"], f"по умолчанию — напрямую: {R['net0']}")
ok(not R["check0"]["check"]["direct"][0] and "укажите прокси" in R["check0"]["check"]["advice"], "«Проверить всё»: сайт не открывается → совет про прокси")
ok(not R["login0"]["ok"] and "«Сеть»" in R["login0"]["error"] and "«Dewiar.pro»" in R["login0"]["error"], f"вход без прокси — понятно, где прокси: {R['login0'].get('error', '')[-90:]}")
ok(not R["bad"]["ok"] and "не сохраняю" in R["bad"]["error"], f"нерабочий прокси не сохраняется: {R['bad'].get('error')}")
ok(not R["mt"]["ok"] and "MTProto" in R["mt"]["error"], "MTProto-прокси — понятный отказ")
st = R["set"]
ok(st["ok"] and st["data"]["on"] and st["data"]["proxy"] == f"socks5://anna:•••@127.0.0.1:{SP}" and st["data"]["check"]["proxy"][0],
   f"ссылка t.me/socks сохранена, проверена, пароль скрыт: {st.get('data', {}).get('proxy')}")
ok(R["login1"]["ok"] and R["login1"]["data"].get("logged_in"), f"вход на https://dewiar.pro — через прокси: {R['login1'].get('error') or 'вошли'}")
ok(not R["upd"].get("error") and R["via"] >= 2, f"проверка обновлений — тоже через прокси (соединений через прокси: {R['via']}): {R['upd'].get('error') or 'ок'}")
ok(R["keep"]["ok"] and R["cfg_keep"] == SOCKS, "«Сохранить» со скрытым паролем не портит сохранённый прокси")
ok(R["off"]["on"] is False and json.loads((tmp / "cfg/settings.json").read_text())["net"]["proxy"] == "", "прокси выключается пустым полем")
ok("s3cret" not in log, "пароль прокси не попал в журнал")
