"""Маршрут на карте (отзыв 13.10: «Лира берёт геоточку, открывает карту и предлагает короткий пешеходный маршрут — дворами»).
Куда — адрес, место, координаты или ссылка на карту (Яндекс, Google, geo:, короткая ссылка — по переадресации). Адрес →
координаты через OpenStreetMap (Nominatim); не нашёлся — карта ищет сама (Google — по тексту). Маршрут строит
приложение карт от того места, где человек сейчас: пешком оно ведёт кратчайшим путём, в том числе дворами и тропинками.
То же в app.js (route*): меняешь здесь — меняй и там."""
import re
import urllib.parse

MODES = {"walk": ("pd", "walking"), "transit": ("mt", "transit"), "car": ("auto", "driving"), "bike": ("bc", "bicycling")}
MODE_RU = {"walk": "пешком", "transit": "на транспорте", "car": "на машине", "bike": "на велосипеде"}
_NUM = r"(-?\d{1,3}\.\d{3,})"          # голые числа в тексте — только с 3+ знаками после точки (не «дом 5.2»)
_ANY = r"(-?\d{1,3}(?:\.\d+)?)"           # в ссылке после geo:, @, q= — любые


def _ok(lat, lon):
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def parse_coords(text):
    """Координаты из текста или ссылки -> (lat, lon) или None. У Яндекса в ll/pt/whatshere — долгота первой."""
    t = urllib.parse.unquote(str(text or ""))
    yandex = "yandex" in t.lower()
    if yandex:                                         # сначала сама точка (pt, «что здесь»), потом маршрут, потом центр карты
        for pat in (r"(?:[?&]pt=|whatshere\[point\]=)", r"rtext=[^&]*~", r"[?&]ll="):
            m = re.search(pat + _ANY + r"\s*,\s*" + _ANY, t)
            if m:
                a, b = float(m.group(1)), float(m.group(2))
                if pat.startswith("rtext"):                # в rtext — широта первой
                    return (a, b) if _ok(a, b) else None
                return (b, a) if _ok(b, a) else None
    for pat in (r"geo:" + _ANY + r",\s*" + _ANY, r"@" + _ANY + r"," + _ANY,
                r"[?&](?:q|query|destination|daddr|ll)=" + _ANY + r"\s*,\s*" + _ANY,
                r"(?<![\d.])" + _NUM + r"\s*[,; ]\s*" + _NUM + r"(?![\d.])"):
        m = re.search(pat, t)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            if _ok(a, b):
                return a, b
    return None


def is_short_link(text):
    return bool(re.search(r"https?://(?:yandex\.\w+/maps/-/|maps\.app\.goo\.gl/|goo\.gl/maps/|go\.2gis\.com/)\S+", str(text or "")))


def geocode(q, get=None):
    """Адрес → (lat, lon, имя) через Nominatim или None. get(url, headers) -> json (для тестов и разных HTTP)."""
    q = str(q or "").strip()
    if not q:
        return None
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1, "accept-language": "ru"})
    if get is None:
        import httpx
        get = lambda u, h: httpx.get(u, headers=h, timeout=10).json()  # noqa: E731
    try:
        d = get(url, {"User-Agent": "Dewiar/1 (https://dewiar.pro)"})
    except Exception:  # noqa: BLE001
        return None
    if not d:
        return None
    try:
        x = d[0]
        lat, lon = float(x["lat"]), float(x["lon"])
    except (KeyError, ValueError, TypeError, IndexError):
        return None
    return (lat, lon, str(x.get("display_name") or q)) if _ok(lat, lon) else None


def build_url(app, dest, origin=None, mode="walk"):
    """dest/origin — (lat, lon) или текст; origin None — от того места, где человек сейчас."""
    yrt, gm = MODES.get(mode, MODES["walk"])
    pt = lambda p: f"{p[0]:.6f},{p[1]:.6f}" if isinstance(p, tuple) else str(p)  # noqa: E731
    if app == "yandex" and isinstance(dest, tuple) and (origin is None or isinstance(origin, tuple)):
        return f"https://yandex.ru/maps/?rtext={pt(origin) if origin else ''}~{pt(dest)}&rtt={yrt}"
    if app == "yandex":                                    # координат нет — Яндекс найдёт место сам, маршрут — кнопкой
        return "https://yandex.ru/maps/?" + urllib.parse.urlencode({"text": pt(dest)})
    q = {"api": 1, "destination": pt(dest), "travelmode": gm}
    if origin:
        q["origin"] = pt(origin)
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(q)


def plan(to, frm="", mode="walk", app="yandex", get=None, resolve=None):
    """-> {url, dest_name, coords, app, mode, found}. resolve(url) -> конечный адрес короткой ссылки (или "")."""
    mode = mode if mode in MODES else "walk"
    to = str(to or "").strip()
    src = to
    if is_short_link(to) and resolve is not None:
        try:
            src = resolve(re.search(r"https?://\S+", to).group(0)) or to
        except Exception:  # noqa: BLE001
            src = to
    c = parse_coords(src)
    name = "" if c else to
    if c is None and not re.match(r"https?://", to):
        g = geocode(to, get)
        if g:
            c, name = (g[0], g[1]), g[2]
    o = None
    if frm:
        o = parse_coords(frm)
        if o is None:
            g = geocode(frm, get)
            o = (g[0], g[1]) if g else frm
    dest = c if c else to
    return {"url": build_url(app, dest, o, mode), "dest_name": name, "coords": c, "app": app, "mode": mode, "found": c is not None}


def say(r, opened=True):
    where = r["dest_name"] or (f"{r['coords'][0]:.5f}, {r['coords'][1]:.5f}" if r["coords"] else "")
    app = "Яндекс Картах" if r["app"] == "yandex" else "Google Картах"
    if not opened:
        return "Ошибка: карта не открылась — скажи об этом и предложи открыть ссылку: " + r["url"]
    if not r["found"] and r["app"] == "yandex":
        return (f"Открыла поиск «{where}» в {app}: точное место не нашлось — пусть выберет его на карте и нажмёт «Маршрут». "
                "Скажи это одной-двумя фразами.")
    return (f"Открыла маршрут {MODE_RU[r['mode']]} до «{where}» в {app}, от того места, где пользователь сейчас."
            + (" Пешком карта ведёт кратчайшим путём — и дворами, и тропинками; время в пути видно на карте." if r["mode"] == "walk" else "")
            + " Скажи коротко, одной-двумя фразами, что маршрут открыт; не выдумывай улицы и время.")
