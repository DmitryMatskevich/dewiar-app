"""Страница через настоящий браузер — когда обычный запрос её не получает. Так работают ИИ-агенты с «руками в браузере».

Зачем (30.10, Лира: «Страница закрыта — сайт не пускает без браузера»): часть сайтов закрыта защитой от ботов
(Cloudflare и подобные: «Just a moment…», 403/503) — обычному HTTP-запросу она отдаёт страницу-проверку на JavaScript,
а настоящий браузер проходит её сам за несколько секунд. Другие сайты отдают пустой HTML, а текст дорисовывают скриптом.

Как: установленный Chromium-браузер (Chrome, Edge, Яндекс Браузер, Brave, Chromium) запускается отдельным процессом
со СВОИМ профилем (STATE_DIR/browser — не ваш обычный профиль, ваши вкладки и пароли не трогаем) и управляется по
протоколу DevTools (CDP) через websockets — новых зависимостей нет. Два режима:
- фоновый (headless) — окна не видно: открыть, дождаться, пока проверка пройдёт сама, забрать текст, закрыть;
- видимый — если сайт просит человека (капча «я не робот», вход в аккаунт): окно открывается у человека, он проходит
  проверку сам, Dewiar читает страницу. Cookies остаются в профиле — следующие заходы на этот сайт снова идут в фоне.
Капчи Dewiar сам не решает и защиту не взламывает: проверку проходит человек.

Стоп-кран «Ничего не уходит в интернет» (egress.local_only) браузер не обходит: при нём режим не запускается вовсе
(отдельный процесс браузера не видит сокетного стоп-крана ядра). Заход записывается в журнал «Что уходило наружу»."""
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("dewiar.browser")

CHALLENGE = re.compile(r"cf-chl|challenge-platform|cf_chl_opt|Just a moment\.\.\.|Attention Required!|Checking your browser|"
                       r"DDoS-Guard|ddos-guard|Проверка браузера|Пожалуйста, подождите|captcha-delivery|"
                       r"g-recaptcha|h-captcha|hcaptcha\.com|turnstile|smartcaptcha", re.I)
HEADLESS_WAIT = 25           # секунд ждём, пока проверка пройдёт сама (обычно 3–8)
VISIBLE_WAIT = 150           # секунд даём человеку пройти проверку в окне
START_WAIT = 20              # секунд на запуск браузера
_lock = threading.Lock()     # один браузер за раз: профиль нельзя открыть двумя процессами
PROFILE = None               # тесты: своя папка профиля вместо STATE_DIR/browser


class NoBrowser(RuntimeError):
    """Нет ни одного подходящего браузера или режим выключен (стоп-кран, тесты)."""


class Blocked(RuntimeError):
    """Сайт так и не пустил: нужна проверка человеком (капча, вход)."""


def _candidates():
    env = os.environ.get("DEWIAR_BROWSER")
    if env:
        yield env
    if sys.platform == "darwin":
        for root in ("/Applications", str(Path.home() / "Applications")):
            for app, exe in (("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"), ("Yandex", "Yandex"),
                             ("Brave Browser", "Brave Browser"), ("Chromium", "Chromium"), ("Vivaldi", "Vivaldi"),
                             ("Google Chrome Canary", "Google Chrome Canary")):
                yield f"{root}/{app}.app/Contents/MacOS/{exe}"
    elif os.name == "nt":
        pf, pf86, la = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"))
        for base in (pf, pf86, la):
            if base:
                yield fr"{base}\Google\Chrome\Application\chrome.exe"
                yield fr"{base}\Microsoft\Edge\Application\msedge.exe"
                yield fr"{base}\BraveSoftware\Brave-Browser\Application\brave.exe"
        if la:
            yield fr"{la}\Yandex\YandexBrowser\Application\browser.exe"
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                     "microsoft-edge-stable", "yandex-browser", "yandex-browser-stable", "brave-browser"):
            p = shutil.which(name)
            if p:
                yield p


def find_browser():
    """Путь к исполняемому файлу Chromium-браузера или None."""
    for p in _candidates():
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def browser_name(path=None):
    p = path or find_browser() or ""
    for key, name in (("yandex", "Яндекс Браузер"), ("edge", "Microsoft Edge"), ("brave", "Brave"), ("vivaldi", "Vivaldi"),
                      ("chromium", "Chromium"), ("chrome", "Google Chrome")):
        if key in p.lower():
            return name
    return "браузер" if p else ""


def is_challenge(text):
    """Страница-проверка вместо содержимого? По первым 6000 знакам HTML или заголовку."""
    return bool(CHALLENGE.search((text or "")[:6000]))


def _blocked_by_policy():
    if os.environ.get("DEWIAR_DRY_TOOLS") or os.environ.get("DEWIAR_EXAM_LAUNCH") or os.environ.get("DEWIAR_NO_BROWSER"):
        return "в тестовом режиме браузер не запускается"
    try:
        import egress
        if egress.local_only():
            return "включён стоп-кран «Ничего не уходит в интернет»"
    except Exception:  # noqa: BLE001 — модуль не загружен (тесты): ограничения нет
        pass
    return ""


class _CDP:
    """Минимальный клиент DevTools: команды по id, события пропускаем; sessionId — команды вкладке."""

    def __init__(self, ws_url):
        from websockets.sync.client import connect
        try:
            self.ws = connect(ws_url, max_size=None, open_timeout=10, proxy=None)   # localhost — мимо системного прокси
        except TypeError:                                                       # websockets < 15: параметра proxy нет
            self.ws = connect(ws_url, max_size=None, open_timeout=10)
        self.n = 0

    def call(self, method, params=None, session=None, timeout=30):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"браузер не ответил на {method}")
            d = json.loads(self.ws.recv(timeout=left))
            if d.get("id") == self.n:
                if "error" in d:
                    raise RuntimeError(f"{method}: {d['error'].get('message')}")
                return d.get("result") or {}

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


class _Browser:
    def __init__(self, exe, profile, visible):
        self.exe, self.profile, self.visible = exe, Path(profile), visible
        self.proc, self.cdp = None, None

    def __enter__(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        port_file = self.profile / "DevToolsActivePort"
        try:
            port_file.unlink()
        except FileNotFoundError:
            pass
        args = [self.exe, f"--user-data-dir={self.profile}", "--remote-debugging-port=0", "--no-first-run",
                "--no-default-browser-check", "--disable-default-apps", "--disable-sync", "--lang=ru-RU",
                "--disable-blink-features=AutomationControlled", "--window-size=1280,900"]
        if not self.visible:
            args += ["--headless=new", "--mute-audio", "--disable-gpu"]
        if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")          # Chromium под root (контейнер, сервер) без этого не стартует
        args.append("about:blank")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     **({"creationflags": 0x08000000} if os.name == "nt" else {}))   # без окна консоли
        end = time.monotonic() + START_WAIT
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise NoBrowser("браузер не запустился (возможно, этот профиль уже открыт)")
            try:
                port, path = port_file.read_text().split("\n")[:2]
                self.cdp = _CDP(f"ws://127.0.0.1:{port.strip()}{path.strip()}")
                return self
            except (FileNotFoundError, ValueError, OSError):
                time.sleep(0.2)
        self._kill()
        raise NoBrowser("браузер не ответил при запуске")

    def __exit__(self, *exc):
        if self.cdp:
            try:
                self.cdp.call("Browser.close", timeout=5)     # штатно: cookies профиля сохраняются на диск
            except Exception:  # noqa: BLE001
                pass
            self.cdp.close()
        try:
            self.proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            self._kill()

    def _kill(self):
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass

    def page(self):
        """Новая вкладка -> sessionId."""
        tid = self.cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = self.cdp.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        if not self.visible:                    # «HeadlessChrome» в User-Agent — первый признак бота для защиты сайтов
            ua = self.cdp.call("Browser.getVersion").get("userAgent", "").replace("HeadlessChrome", "Chrome")
            if ua:
                self.cdp.call("Emulation.setUserAgentOverride", {"userAgent": ua, "acceptLanguage": "ru-RU,ru;q=0.9,en;q=0.8"},
                              session=sid)
        else:
            self.cdp.call("Page.bringToFront", session=sid)
        return sid

    def js(self, sid, expr, timeout=15):
        r = self.cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, session=sid, timeout=timeout)
        return (r.get("result") or {}).get("value")


_STATE_JS = ("JSON.stringify({ready: document.readyState, title: document.title, url: location.href, "
             "head: (document.documentElement ? document.documentElement.outerHTML.slice(0, 6000) : ''), "
             "len: (document.body ? document.body.innerText.length : 0)})")


def _wait(b, sid, seconds, need_text=True):
    """Ждём: загрузилась, проверка прошла, текст появился. -> состояние или None (проверка так и висит)."""
    end, st = time.monotonic() + seconds, {}
    while time.monotonic() < end:
        try:
            st = json.loads(b.js(sid, _STATE_JS) or "{}")
        except (RuntimeError, TimeoutError, ValueError):
            st = {}
        if st.get("ready") in ("interactive", "complete") and not is_challenge(st.get("title", "") + st.get("head", "")) \
                and (not need_text or st.get("len", 0) > 200 or st.get("ready") == "complete"):
            return st
        time.sleep(0.7)
    return None


def fetch(url, visible=False, profile=None, wait=None):
    """Открыть url в браузере -> {"html", "text", "url", "title", "browser"}.
    visible=False — фоном; Blocked — сайт просит человека. visible=True — окно у человека, ждём до VISIBLE_WAIT секунд.
    NoBrowser — браузера нет или режим запрещён."""
    why = _blocked_by_policy()
    if why:
        raise NoBrowser(why)
    exe = find_browser()
    if not exe:
        raise NoBrowser("не найден Chrome, Edge, Яндекс Браузер, Brave или Chromium")
    if not profile:
        if PROFILE:
            profile = PROFILE
        else:
            from settings import STATE_DIR
            profile = Path(STATE_DIR) / "browser"
    try:
        import egress
        egress.note(urlparse(url).hostname or "", kind=("Страница через браузер Dewiar", "amber"))
    except Exception:  # noqa: BLE001
        pass
    with _lock, _Browser(exe, profile, visible) as b:
        sid = b.page()
        b.cdp.call("Page.navigate", {"url": url}, session=sid, timeout=30)
        time.sleep(0.5)
        st = _wait(b, sid, wait or (VISIBLE_WAIT if visible else HEADLESS_WAIT))
        if not st:
            raise Blocked("сайт просит пройти проверку «я не робот»" if not visible else
                          "проверку в окне браузера не прошли за отведённое время")
        time.sleep(0.8)                                         # дорисовка скриптами после загрузки
        html = b.js(sid, "document.documentElement.outerHTML", timeout=30) or ""
        text = b.js(sid, "document.body ? document.body.innerText : ''", timeout=30) or ""
        log.info("Страница через браузер (%s, %s): %s, %d знаков", browser_name(exe), "окно" if visible else "фон",
                 urlparse(url).hostname, len(text))
        return {"html": html, "text": text, "url": st.get("url") or url, "title": st.get("title", ""),
                "browser": browser_name(exe)}


if __name__ == "__main__":                       # ручная проверка: python core/browserpage.py URL [--visible]
    logging.basicConfig(level=logging.INFO)
    u = sys.argv[1]
    try:
        r = fetch(u, visible="--visible" in sys.argv, profile=Path(tempfile.gettempdir()) / "dewiar-browser-test")
        print(r["browser"], r["url"], r["title"], "\n", r["text"][:1500])
    except (NoBrowser, Blocked) as e:
        print(type(e).__name__, e)
