"""Безопасность: стоп-кран «Ничего не уходит в интернет» и журнал «Что уходило наружу» (21.10, студия → «Безопасность»).

Одно место на весь процесс ядра — сокет: socket.socket.connect. Всё сетевое в Dewiar (httpx — модели, облако, синхронизация,
«Близкие», поиск, Telegram, MAX; почта smtplib/imaplib; yt-dlp; urllib) открывает соединение через него. Поэтому:
- стоп-кран включён (privacy.local_only) — соединение с адресом вне компьютера и домашней сети не открывается вовсе
  (LocalOnlyError), какая бы часть программы его ни просила; домашняя сеть (своя модель на соседнем компьютере, свой
  сервер Dewiar дома) — можно: это не интернет;
- каждое соединение наружу записывается в журнал: куда (имя узла — из getaddrinfo), что это (по узлу и пути запроса —
  httpx сообщает путь и объём), сколько раз и сколько байт. Журнал — за сегодня и вчера, в памяти и в STATE_DIR/egress.json;
  тексты разговоров, ключи и адреса страниц целиком в него не пишутся — только узел и вид.
Пароли сейфа наружу не уходят вообще. Звук уходит, только когда слух — на сервере (строка «Распознавание речи» в журнале);
файлы — только по вашей просьбе («Близкие», почта)."""
import ipaddress
import json
import logging
import socket
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger("dewiar.egress")


class LocalOnlyError(ConnectionRefusedError):
    """Соединение наружу при включённом стоп-кране."""
    def __init__(self, host=""):
        super().__init__(f"включён режим «Ничего не уходит в интернет» — {host or 'адрес'} вне домашней сети, соединение не открыто "
                         "(выключить — студия → «Безопасность»)")


_state = {"on": False, "installed": False, "names": {}, "path": None, "settings": None, "llm_hosts": set()}
_lock = threading.Lock()
_day = {}          # {"YYYY-MM-DD": {"kind|host": {kind, host, n, out, inn, last}}}


def local_ip(ip):
    """Адрес этого компьютера или домашней сети (не интернет)."""
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    return a.is_loopback or a.is_private or a.is_link_local or a.is_unspecified


def local_host(host):
    h = str(host or "").strip("[]").lower()
    if not h:
        return False
    if h in ("localhost",) or h.endswith(".local") or h.endswith(".localhost"):
        return True
    try:
        return local_ip(h)
    except ValueError:
        return False


def set_local_only(on):
    _state["on"] = bool(on)


def local_only():
    return bool(_state["on"])


class DewiarServicesDisabled(ConnectionRefusedError):
    """Vendor services are disabled independently of other internet connections."""


def dewiar_services_enabled():
    settings = _state["settings"]
    return bool(settings and settings.get("privacy", "dewiar_services"))


def check_dewiar_host(host):
    host = (host.decode() if isinstance(host, bytes) else str(host or "")).lower().rstrip(".")
    if not dewiar_services_enabled() and any(host == domain or host.endswith("." + domain)
                                           for domain in ("dewiar.pro", "dewiar.ru")):
        raise DewiarServicesDisabled("Сервисы Dewiar отключены: " + host)


# ---------- журнал ----------

def _kind(host, path=""):
    """Что это за соединение — словами для человека. -> (вид, цвет: amber — открыто, blue — шифровано, grey — служебное)."""
    h, p = (host or "").lower(), unquote(path or "").lower()
    if h in _state["llm_hosts"] and ("chat/completions" in p or "/messages" in p or not p):
        return "Вопрос модели", "amber"
    if "dewiar" in h or "/dwrapp/" in p or "cloud.php" in p:
        if "audio/transcriptions" in p:
            return "Распознавание речи — облако Dewiar", "amber"
        if "audio/speech" in p:
            return "Голос — облако Dewiar", "amber"
        if "chat/completions" in p:
            return "Вопрос модели — Dewiar Облако", "amber"
        if "/sync" in p:
            try:
                e2e = bool(_state["settings"] and _state["settings"].get("sync", "e2e"))
            except Exception:  # noqa: BLE001
                e2e = False
            return ("Синхронизация — зашифровано", "blue") if e2e else ("Синхронизация — без шифрования на устройстве", "amber")
        if "/close" in p:
            return "«Близкие» — зашифровано", "blue"
        if "/pulse" in p:
            return "Пульс программы — без разговоров", "grey"
        if "/report" in p:
            return "Отчёт разработчику — по вашей кнопке", "grey"
        if "latest.json" in p or "/download" in p or "/models/" in p:
            return "Проверка и загрузка обновлений", "grey"
        if "/search" in p or "/web" in p or "/fetch" in p:
            return "Поиск в интернете — через сервер Dewiar", "amber"
        return "Сервер Dewiar", "grey"
    if "telegram" in h:
        return "Telegram-бот", "amber"
    if "max.ru" in h or "oneme" in h:
        return "MAX-бот", "amber"
    if "huggingface" in h or "github" in h or "hf.co" in h:
        return "Загрузка моделей и голосов", "grey"
    if h in _state["llm_hosts"]:
        return "Вопрос модели", "amber"
    return "Интернет: " + h, "amber"


def note(host, path="", out=0, inn=0, kind=None):
    """Запись в журнал: соединение или запрос наружу."""
    if not host or local_host(host):
        return
    k, color = kind or _kind(host, path)
    day = time.strftime("%Y-%m-%d")
    with _lock:
        d = _day.setdefault(day, {})
        for old in [x for x in _day if x < time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))]:
            _day.pop(old, None)
        key = k + "|" + host
        e = d.setdefault(key, {"kind": k, "color": color, "host": host, "n": 0, "out": 0, "inn": 0, "first": time.time(), "last": 0})
        e["n"] += 1
        e["out"] += int(out or 0)
        e["inn"] += int(inn or 0)
        e["last"] = time.time()


def journal(day=None):
    """-> [{kind, color, host, n, out, inn, first, last}] за день (по умолчанию сегодня), свежие сверху."""
    day = day or time.strftime("%Y-%m-%d")
    with _lock:
        rows = [dict(v) for v in (_day.get(day) or {}).values()]
    return sorted(rows, key=lambda r: -r["last"])


def save():
    p = _state["path"]
    if not p:
        return
    try:
        with _lock:
            data = json.dumps(_day, ensure_ascii=False)
        Path(p).write_text(data, encoding="utf-8")
    except OSError:
        pass


def _load():
    p = _state["path"]
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8")) if p else {}
        if isinstance(d, dict):
            _day.update({k: v for k, v in d.items() if isinstance(v, dict)})
    except (OSError, ValueError):
        pass


# ---------- сокет: одно место на весь процесс ----------

def install(settings=None, path=None):
    """Поставить стоп-кран и журнал на сокеты процесса (один раз). settings — чтобы знать адреса моделей (вид «Вопрос модели»)."""
    if _state["installed"]:
        return
    _state.update(installed=True, settings=settings, path=path)
    _load()
    names = _state["names"]
    real_gai = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def gai(host, *a, **k):
        check_dewiar_host(host)
        res = real_gai(host, *a, **k)
        try:
            if isinstance(host, (str, bytes)) and host:
                hs = host.decode() if isinstance(host, bytes) else host
                for r in res[:8]:
                    names[r[4][0]] = hs
                if len(names) > 4000:
                    names.clear()
        except Exception:  # noqa: BLE001
            pass
        return res

    def check(sock, address):
        try:
            # только TCP: UDP-«соединение» ничего не отправляет (так узнают свой адрес в сети: connect(("8.8.8.8", 80)))
            if sock.family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple) or not (sock.type & socket.SOCK_STREAM):
                return
            ip = address[0]
        except Exception:  # noqa: BLE001
            return
        if local_ip(ip):
            return
        host = names.get(ip, ip)
        check_dewiar_host(host)
        if _state["on"]:
            log.info("Стоп-кран: соединение с %s не открыто", host)
            raise LocalOnlyError(host)
        if not getattr(_tls, "noted", False):        # запрос httpx запишет себя сам — с путём и объёмом
            note(host)

    def connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        try:
            check(self, address)
        except (LocalOnlyError, DewiarServicesDisabled):
            import errno
            return errno.ECONNREFUSED
        return real_connect_ex(self, address)

    socket.getaddrinfo = gai
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    _hook_httpx()
    threading.Thread(target=_saver, daemon=True, name="egress-save").start()


_tls = threading.local()


def _hook_httpx():
    """Запросы httpx — с путём и объёмом (вид «Распознавание речи», «Синхронизация»…), без текста и ключей."""
    try:
        import httpx
    except ImportError:
        return
    real_send = httpx.Client.send

    def send(self, request, *a, **k):
        host = request.url.host or ""
        check_dewiar_host(host)
        if not local_host(host):
            if _state["on"]:
                raise httpx.ConnectError(str(LocalOnlyError(host)), request=request)
            _tls.noted = True
        try:
            r = real_send(self, request, *a, **k)
        finally:
            _tls.noted = False
        if not local_host(host):
            try:
                size_out = int(request.headers.get("content-length") or 0)
                size_in = int(r.headers.get("content-length") or 0)
            except (ValueError, TypeError):
                size_out = size_in = 0
            try:                                   # путь с запросом: у облака Dewiar вид — в ?p=… (cloud.php?p=chat/completions)
                path = request.url.raw_path.decode("ascii", "replace")
            except Exception:  # noqa: BLE001
                path = request.url.path
            note(host, path, size_out, size_in)
        return r
    httpx.Client.send = send


def llm_hosts(urls):
    """Адреса моделей из профилей раздела «Модели» — чтобы журнал писал «Вопрос модели», а не «Интернет: …»."""
    hs = set()
    for u in urls or []:
        try:
            h = (urlparse(u if "://" in u else "http://" + u).hostname or "").lower()
            if h:
                hs.add(h)
        except ValueError:
            pass
    _state["llm_hosts"] = hs


def _saver():
    while True:
        time.sleep(60)
        save()
