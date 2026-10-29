"""«Ваши данные»: что Dewiar хранит и куда что уходит — для помощницы (вопросы о конфиденциальности, персональных
данных, «кто видит наши разговоры»). Текст общий со студией и телефоном: ui/assets/privacy.js (JSON после «=»).
К фактам добавляется, как устроено именно у этого человека: где думает модель и включена ли синхронизация."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

SRC = Path(__file__).resolve().parent.parent / "ui" / "assets" / "privacy.js"
_cache = None


def data():
    global _cache
    if _cache is None:
        try:
            raw = SRC.read_text(encoding="utf-8")
            _cache = json.loads(raw[raw.index("=") + 1:raw.index("};") + 1])
        except Exception:  # noqa: BLE001
            _cache = {"title": "Ваши данные", "items": []}
    return _cache


def where_model(base_url: str) -> str:
    """Где думает модель — словами для человека."""
    u = (base_url or "").strip()
    if not u:
        return "модель ещё не подключена"
    host = (urlparse(u if "://" in u else "http://" + u).hostname or "").lower()
    if "dewiar" in host or "cloud.php" in u or "/dwrapp/" in u:     # облако Dewiar и по внутреннему адресу сервера
        return "в облаке Dewiar (наши серверы; текст разговоров не сохраняется)"
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local") or re.match(r"^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.", host):
        return "на компьютере или сервере пользователя — разговор никуда не уходит"
    return f"во внешнем сервисе {host} по ключу пользователя — вопросы уходят туда"


def rule(base_url: str = "", sync_on: bool = False) -> str:
    """Для системной подсказки: факты и как сейчас у этого пользователя."""
    d = data()
    facts = " ".join(f"{t}: {x}" for t, x in d.get("items", []))
    return ("ДАННЫЕ И КОНФИДЕНЦИАЛЬНОСТЬ. Спросят, где хранятся данные, кто видит разговоры, как защищены персональные данные, — "
            "ответь спокойно и по сути, своими словами, в 2–4 предложениях, из этих фактов: " + facts
            + " Главное скажи обязательно: заметки, память и файлы лежат у пользователя — на этом компьютере (в папке ассистента),"
            " а не на чужом сервере; где думает модель — ниже."
            + f" Сейчас у этого пользователя ты думаешь {where_model(base_url)}; синхронизация {'включена' if sync_on else 'выключена'}."
            " Подробно — в настройках («Безопасность» → «Ваши данные»). Не выдумывай того, чего здесь нет: сертификатов, законов, сроков хранения.")
