#!/usr/bin/env python3
"""Проверка подключения Яндекс Музыки для Dewiar: вход по коду и текст песни с метками времени (LRC).

План: plan/song-lyrics-chords-plan.md, раздел 6.4. Нужен только Python 3.8+, ничего не устанавливает.

Запуск на Mac из каталога приложения:
    python3 tools/yandex_music_auth_check.py                 # вход по коду — основной способ
    python3 tools/yandex_music_auth_check.py --refresh       # заодно проверить продление токена
    python3 tools/yandex_music_auth_check.py --paste         # запасной способ: проверить готовый токен
    python3 tools/yandex_music_auth_check.py --track https://music.yandex.ru/album/…/track/…

Токен никуда не сохраняется и целиком не выводится — в отчёте только начало и конец.
Отчёт можно присылать как есть.
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

OAUTH_URL = "https://oauth.yandex.ru"
API_URL = "https://api.music.yandex.net"
# Публичные данные приложения Яндекс Музыки для Android — их же использует библиотека yandex-music (MarshalX).
CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
CLIENT_SECRET = "53bc75238f0c4d08a118e51fe9203300"
SIGN_KEY = b"p93jhgh689SBReK6ghtw62"          # подпись запроса текста (yandex_music/utils/sign_request.py)
MUSIC_CLIENT = "YandexMusicAndroid/24023621"   # заголовок X-Yandex-Music-Client, как в библиотеке
USER_AGENT = "Dewiar-check/1.0"
DEFAULT_QUERY = "Кино Кукушка"
LRC_LINE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
SLEEP = time.sleep                             # в тестах подменяется


class Fail(Exception):
    """Проверка не прошла: текст — для человека."""


class ApiError(Fail):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def _ssl_context():
    try:                                       # у Python с python.org нет системных сертификатов — certifi, если есть
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class Http:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.ctx = _ssl_context()

    def call(self, method, url, params=None, data=None, headers=None):
        """→ (код ответа, разобранный JSON или текст). Нет связи — Fail с понятным текстом."""
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "ru"}
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        host = urllib.parse.urlsplit(url).netloc
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e.reason):
                raise Fail("не проверен сертификат HTTPS. Для Python с python.org запустите «Install Certificates.command» "
                           "(Программы → Python 3.x) или выполните: pip3 install certifi") from None
            raise Fail(f"нет связи с {host}: {e.reason}") from None
        except (TimeoutError, OSError) as e:
            raise Fail(f"нет связи с {host}: {e}") from None
        text = raw.decode("utf-8", "replace")
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text


class Report:
    """Печатает шаги сразу и прячет секреты в любом выводе."""
    MARK = {"ok": "✔", "fail": "✖", "skip": "–", "info": "i"}

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.rows = []
        self.secrets = []

    def secret(self, value):
        if value and value not in self.secrets:
            self.secrets.append(value)

    def clean(self, text):
        text = str(text)
        for s in self.secrets:
            text = text.replace(s, mask(s))
        return text

    def say(self, text=""):
        print(self.clean(text), file=self.out, flush=True)

    def step(self, kind, name, detail=""):
        self.rows.append((kind, name, self.clean(detail)))
        self.say(f"{self.MARK[kind]} {name}" + (f" — {detail}" if detail else ""))

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "fail"]


def mask(token):
    token = token or ""
    return f"{token[:4]}…{token[-4:]} ({len(token)} симв.)" if len(token) > 12 else "***"


def short(value, limit=200):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def token_kind(token):
    if token.startswith("y0_"):
        return "новый вид y0_…"
    if token.startswith("AQAAAA"):
        return "прежний вид AQAAAA…"
    return "незнакомый вид — проверить, что Dewiar его принимает"


def parse_pasted(text):
    """Токен из вставки: сам токен, строка access_token=…&…, или адрес …#access_token=…"""
    text = text.strip()
    if "access_token=" not in text:
        return text, {}
    part = text.split("#", 1)[1] if "#" in text else text.split("?", 1)[-1]
    qs = urllib.parse.parse_qs(part)
    extra = {}
    exp = (qs.get("expires_in") or [""])[0]
    if exp.isdigit():
        extra["expires_in"] = int(exp)
    return (qs.get("access_token") or [""])[0], extra


def track_id_from(value):
    value = (value or "").strip()
    m = re.search(r"/track/(\d+)", value) or re.fullmatch(r"(\d+)(?::\d+)?", value)
    if not m:
        raise Fail(f"не понял номер трека: {value!r} (нужна ссылка …/track/123 или число)")
    return m.group(1)


# ---------- вход по коду (OAuth Device Flow) ----------

def request_code(http, oauth_url, device_id):
    status, js = http.call("POST", oauth_url + "/device/code",
                           data={"client_id": CLIENT_ID, "device_id": device_id, "device_name": "Dewiar check"})
    if status != 200 or not isinstance(js, dict) or not js.get("device_code"):
        raise Fail(f"сервер ответил {status}: {short(js)}")
    return js


def poll_token(http, oauth_url, code, rep):
    """Опрос до подтверждения. slow_down → интервал +5 с (в библиотеке yandex-music это ошибка)."""
    interval = max(1, int(code.get("interval") or 5))
    deadline = time.monotonic() + int(code.get("expires_in") or 300)
    slowed = 0
    while True:
        if time.monotonic() >= deadline:
            raise Fail("код устарел — запустите проверку снова")
        SLEEP(interval)
        status, js = http.call("POST", oauth_url + "/token",
                               data={"grant_type": "device_code", "code": code["device_code"],
                                     "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        if status == 200 and isinstance(js, dict) and js.get("access_token"):
            return js, interval, slowed
        err = js.get("error") if isinstance(js, dict) else None
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            slowed += 1
            rep.say(f"  сервер просит опрашивать реже — интервал {interval} с")
            continue
        if err in ("expired_token", "bad_verification_code", "invalid_grant"):
            raise Fail("код устарел или неверен — запустите проверку снова")
        if err == "access_denied":
            raise Fail("вход отклонён на странице Яндекса")
        raise Fail(f"ошибка {status}: {err or short(js)}")


def refresh(http, oauth_url, refresh_token):
    status, js = http.call("POST", oauth_url + "/token",
                           data={"grant_type": "refresh_token", "refresh_token": refresh_token,
                                 "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    if status != 200 or not isinstance(js, dict) or not js.get("access_token"):
        raise Fail(f"продление не принято ({status}): {short(js)}")
    return js


# ---------- API Яндекс Музыки ----------

def api(http, api_url, token, path, params=None):
    status, js = http.call("GET", api_url + path, params=params,
                           headers={"Authorization": f"OAuth {token}", "X-Yandex-Music-Client": MUSIC_CLIENT})
    if status in (401, 403):
        raise ApiError(status, f"токен не принят ({status})")
    if status != 200:
        raise ApiError(status, f"ответ {status}: {short(js)}")
    return js.get("result") if isinstance(js, dict) and "result" in js else js


def account(http, api_url, token):
    res = api(http, api_url, token, "/account/status") or {}
    acc = res.get("account") or {}
    who = acc.get("login") or acc.get("displayName") or acc.get("uid")
    if not who:
        raise Fail(f"в ответе нет учётной записи: {short(res)}")
    plus = (res.get("plus") or {}).get("hasPlus")
    return f"вход: {who}; Плюс: {'да' if plus else 'нет' if plus is False else 'не указано'}"


def find_track(http, api_url, token, query):
    res = api(http, api_url, token, "/search", {"text": query, "type": "track", "page": 0, "nocorrect": "False"}) or {}
    items = ((res.get("tracks") or {}).get("results")) or []
    if not items:
        raise Fail(f"по запросу «{query}» треков нет")
    t = items[0]
    artists = ", ".join(a.get("name", "") for a in t.get("artists") or [])
    return str(t.get("id")).split(":")[0], f"{artists} — {t.get('title', '?')}"


def lyrics(http, api_url, token, track_id, fmt):
    ts = int(time.time())
    sign = base64.b64encode(hmac.new(SIGN_KEY, f"{track_id}{ts}".encode(), hashlib.sha256).digest()).decode()
    res = api(http, api_url, token, f"/tracks/{track_id}/lyrics", {"format": fmt, "timeStamp": ts, "sign": sign}) or {}
    url = res.get("downloadUrl") or res.get("download_url")
    if not url:
        raise Fail(f"в ответе нет ссылки на текст: {short(res)}")
    status, body = http.call("GET", url)
    if status != 200:
        raise Fail(f"текст не скачался ({status})")
    return body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)


# ---------- проверка ----------

def run(args, rep, http):
    token, info = "", {}

    if args.paste:
        rep.say("Вставьте токен или строку access_token=… (ввод не отображается) и нажмите Enter:")
        token, info = parse_pasted(getpass.getpass(""))
        rep.secret(token)
        if not token:
            rep.step("fail", "Токен", "пустой ввод")
            return
        rep.step("ok", "Токен получен из вставки", mask(token))
    else:
        device_id = "dewiar-check-" + secrets.token_hex(4)
        try:
            code = request_code(http, args.oauth_url, device_id)
        except Fail as e:
            rep.step("fail", "Запрос кода входа", str(e))
            return
        rep.step("ok", "Запрос кода входа", f"код действует {int(code.get('expires_in') or 0) // 60} мин")
        url = code.get("verification_url") or "https://ya.ru/device"
        rep.say("")
        rep.say(f"  Откройте: {url}")
        rep.say(f"  Введите код: {code.get('user_code')}")
        rep.say("  и подтвердите вход. Жду… (Ctrl+C — отмена)")
        rep.say("")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass
        try:
            info, interval, slowed = poll_token(http, args.oauth_url, code, rep)
        except Fail as e:
            rep.step("fail", "Подтверждение входа", str(e))
            return
        token = info["access_token"]
        rep.secret(token)
        rep.secret(info.get("refresh_token"))
        rep.step("ok", "Подтверждение входа", f"токен {mask(token)}" + (f"; «реже» просили {slowed} раз" if slowed else ""))

    exp = info.get("expires_in")
    rep.step("info", "Вид токена", token_kind(token) + (f"; срок {int(exp) // 86400} дн." if exp else ""))
    if not args.paste:
        rep.step("info", "Токен для продления", "выдан" if info.get("refresh_token") else
                 "не выдан — раз в год понадобится повторный вход")

    try:
        rep.step("ok", "Статус аккаунта", account(http, args.api_url, token))
    except Fail as e:
        rep.step("fail", "Статус аккаунта", str(e))
        return

    track_id, label = None, ""
    try:
        if args.track:
            track_id, label = track_id_from(args.track), f"трек {track_id_from(args.track)}"
        else:
            track_id, label = find_track(http, args.api_url, token, args.query)
        rep.step("ok", "Трек для проверки текста", f"{label} (id {track_id})")
    except Fail as e:
        rep.step("fail", "Трек для проверки текста", str(e))

    if track_id:
        try:
            text = lyrics(http, args.api_url, token, track_id, "LRC")
            timed = [ln for ln in text.splitlines() if LRC_LINE.match(ln.strip())]
            if timed:
                rep.step("ok", "Текст с метками времени (LRC)", f"строк с метками времени: {len(timed)}")
                for ln in timed[:3]:
                    rep.say("    " + ln.strip()[:70])
            else:
                rep.step("fail", "Текст с метками времени (LRC)", f"меток времени нет: {short(text, 120)}")
        except ApiError as e:
            if e.status == 404:
                note = "у этого трека нет текста с метками времени"
                try:
                    lyrics(http, args.api_url, token, track_id, "TEXT")
                    note += " (обычный текст есть)"
                except Fail:
                    note += " (и обычного текста нет)"
                rep.step("info", "Текст с метками времени (LRC)", note + " — попробуйте другую песню: --track …")
            else:
                rep.step("fail", "Текст с метками времени (LRC)", str(e))
        except Fail as e:
            rep.step("fail", "Текст с метками времени (LRC)", str(e))

    if args.refresh and not args.paste:
        if not info.get("refresh_token"):
            rep.step("skip", "Продление токена", "токен для продления не выдан")
        else:
            try:
                new = refresh(http, args.oauth_url, info["refresh_token"])
                rep.secret(new["access_token"])
                rep.secret(new.get("refresh_token"))
                account(http, args.api_url, new["access_token"])
                rep.step("ok", "Продление токена", f"новый токен {mask(new['access_token'])} принят API")
            except Fail as e:
                rep.step("fail", "Продление токена", str(e))

    try:
        import yandex_music  # noqa: F401 — необязательная проверка совместимости с библиотекой
    except ImportError:
        rep.step("skip", "Библиотека yandex-music", "не установлена — проверка совместимости пропущена")
    else:
        try:
            from yandex_music import Client
            me = Client(token).init().me
            who = getattr(getattr(me, "account", None), "login", None) or "?"
            rep.step("ok", "Библиотека yandex-music", f"версия {getattr(yandex_music, '__version__', '?')}, токен принят ({who})")
        except Exception as e:  # noqa: BLE001
            rep.step("fail", "Библиотека yandex-music", f"{type(e).__name__}: {e}")


def main(argv=None, out=None):
    p = argparse.ArgumentParser(description="Проверка подключения Яндекс Музыки для Dewiar (вход по коду, тексты LRC).")
    p.add_argument("--paste", action="store_true", help="проверить готовый токен (запасной способ) вместо входа по коду")
    p.add_argument("--track", help="ссылка на трек или его номер для проверки текста")
    p.add_argument("--query", default=DEFAULT_QUERY, help=f"поиск трека, если --track не задан (по умолчанию «{DEFAULT_QUERY}»)")
    p.add_argument("--refresh", action="store_true", help="проверить продление токена")
    p.add_argument("--no-browser", action="store_true", help="не открывать страницу входа автоматически")
    p.add_argument("--timeout", type=float, default=20, help="таймаут запроса, с")
    p.add_argument("--oauth-url", default=OAUTH_URL, help=argparse.SUPPRESS)
    p.add_argument("--api-url", default=API_URL, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    rep = Report(out)
    rep.say("Проверка подключения Яндекс Музыки для Dewiar\n")
    try:
        run(args, rep, Http(args.timeout))
    except KeyboardInterrupt:
        rep.step("fail", "Проверка", "отменена")
    rep.say("")
    rep.say("Токен нигде не сохранён. В Dewiar он будет получен заново кнопкой «Подключить Яндекс Музыку».")
    if rep.failed:
        rep.say(f"Итог: есть ошибки ({len(rep.failed)}). Пришлите этот отчёт — токена в нём нет.")
        return 1
    rep.say("Итог: всё работает.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
