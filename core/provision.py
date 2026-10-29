"""Подключение одним адресом, импорт и экспорт настроек.

Файл-описание (манифест) Dewiar — JSON:
{
  "dewiar": 1,
  "name": "Dewiar Cloud",                          # как показать пользователю
  "url": "https://dewiar.ru/.well-known/dewiar.json",  # откуда обновлять (подставляется сам)
  "key": {"required": true, "hint": "Код из личного кабинета"},   # необязательно
  "llm": {"active": "dewiar", "profiles": {"dewiar": {"name": "...", "kind": "openai",
          "base_url": "https://dewiar.ru/v1", "api_key": "${KEY}", "model": ""}}},
  "stt": {"engine": "remote", "remote": {"base_url": "...", "api_key": "${KEY}"}},
  "tts": {"engine": "remote", "remote": {"base_url": "...", "model": "...", "sample_rate": 24000}},
  "skills": [{"name": "morning", "url": "https://dewiar.ru/skills/morning.zip"}],
  "mcp": {"servers": {"files": {"type": "http", "url": "https://..."}}},
  "persona": "lira", "audio": {...}, "tools": {...}
}
${KEY} заменяется кодом доступа пользователя.

Откуда берётся:
  • адрес сервера → https://<адрес>/.well-known/dewiar.json (или /dewiar.json);
  • файл .dewiar (экспорт/импорт, двойной клик);
  • ссылка dewiar://connect?server=<адрес>;
  • /opt/dewiar/data/provision.json в пакете — применяется сам при первом запуске (is_fresh).

Порядок: fetch_manifest → preview (показать человеку, с предупреждениями) → to_patch → apply_patch.
Безопасность: экспорт по умолчанию стирает ключи и пароли; MCP-серверы stdio и run_command из чужого манифеста
запускают программы на компьютере — preview выносит их в предупреждения.
"""

import copy
import json
import logging
import re
import time
from pathlib import Path

import httpx

from settings import DATA_DIR, DEFAULTS

log = logging.getLogger("dewiar.provision")

SECTIONS = ("persona", "all_names", "user_name", "llm", "stt", "tts", "audio", "tools", "mcp")   # что переносят манифест и экспорт
SECRET_KEYS = {"api_key", "token", "authorization", "password", "secret"}   # поля с такими именами на любой глубине — секреты


class ProvisionError(Exception):
    pass


def _walk(obj, fn, path=()):
    """Копия obj, где каждое значение-лист заменено на fn(значение, путь из ключей)."""
    if isinstance(obj, dict):
        return {k: _walk(v, fn, path + (k,)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fn, path + (str(i),)) for i, v in enumerate(obj)]
    return fn(obj, path)


def strip_secrets(obj):
    """Копия без секретов: значения полей из SECRET_KEYS стёрты (имя поля без учёта регистра)."""
    return _walk(obj, lambda v, p: "" if p and p[-1].lower() in SECRET_KEYS and v else v)


def fill_key(obj, key):
    """Копия, где ${KEY} во всех строках заменён кодом доступа."""
    return _walk(obj, lambda v, p: v.replace("${KEY}", key) if isinstance(v, str) else v)


def export_settings(snapshot, include_secrets=False, extra=None):
    """Снимок настроек -> содержимое файла .dewiar (разделы SECTIONS + extra). Ключи и пароли — только по явной
    просьбе (include_secrets): такой файл легко переслать."""
    data ={"dewiar": 1, "name": "Мои настройки Dewiar", "exported": time.strftime("%Y-%m-%d %H:%M")}
    for k in SECTIONS:
        if k in snapshot:
            data[k] = copy.deepcopy(snapshot[k])
    data.pop("ui", None)
    if extra:
        data.update(extra)
    return data if include_secrets else strip_secrets(data)


def normalize_address(addr):
    """Адрес или ссылка dewiar://connect?server=… -> адрес со схемой, без «/» в конце. Без схемы: IP или localhost —
    http:// (свой сервер в локальной сети), доменное имя — https://."""
    addr = addr.strip()
    if not addr:
        raise ProvisionError("укажите адрес сервера")
    if addr.startswith("dewiar://"):
        m = re.search(r"[?&]server=([^&]+)", addr)
        if not m:
            raise ProvisionError("в ссылке нет адреса сервера")
        from urllib.parse import unquote
        addr = unquote(m.group(1))
    if not re.match(r"^https?://", addr):
        # без шифрования — только свой сервер в локальной сети (IP, localhost); доменное имя, даже с портом, — https:
        # манифест несёт ключи доступа, по открытому http их увидел бы любой в сети
        addr = ("http://" if re.match(r"^(\d+\.){3}\d+|^localhost\b|^\[", addr) else "https://") + addr
    return addr.rstrip("/")


def fetch_manifest(addr, timeout=8):
    """Адрес сервера → манифест. Адрес на .json — берётся как есть, иначе пробуем /.well-known/dewiar.json
    и /dewiar.json. В манифест записывается url, откуда он взят, — по нему потом обновляться."""
    base = normalize_address(addr)
    cands = [base] if base.endswith(".json") else [base + "/.well-known/dewiar.json", base + "/dewiar.json"]
    last = None
    for url in cands:
        try:
            r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                validate(data)
                data.setdefault("url", url)
                return data
            last = f"{url}: HTTP {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            last = f"{url}: {e}"
    raise ProvisionError(f"не нашёл настройки Dewiar на {base} ({last})")


def validate(data):
    """Проверить, что это манифест Dewiar ("dewiar": 1) и разделы-словари не испорчены -> data."""
    if not isinstance(data, dict) or data.get("dewiar") != 1:
        raise ProvisionError("это не файл настроек Dewiar")
    for k in ("llm", "stt", "tts", "audio", "tools", "mcp"):
        if k in data and not isinstance(data[k], dict):
            raise ProvisionError(f"раздел «{k}» повреждён")
    return data


def preview(data, current):
    """Что изменится — человеческим языком -> {name, lines, warnings, need_key, key_hint, source}.
    В warnings — то, что запустит программы на компьютере (MCP stdio, команды терминала): человек должен
    увидеть это до применения."""
    validate(data)
    lines, warnings = [], []
    llm = data.get("llm") or {}
    for pid, p in (llm.get("profiles") or {}).items():
        lines.append(f"Модели: {p.get('name') or pid} — {p.get('base_url', '')}{' · ' + p['model'] if p.get('model') else ''}")
    if llm.get("active"):
        lines.append(f"Активная модель: {(llm.get('profiles') or {}).get(llm['active'], {}).get('name') or llm['active']}")
    stt, tts = data.get("stt") or {}, data.get("tts") or {}
    if stt:
        lines.append("Слух: " + ({"remote": f"сервер {stt.get('remote', {}).get('base_url', '')}", "gigaam": "GigaAM на компьютере",
                                   "vosk": "Vosk"}.get(stt.get("engine"), stt.get("engine") or "без изменений")))
    if tts:
        lines.append("Голос: " + ({"remote": f"сервер {tts.get('remote', {}).get('base_url', '')}", "piper": "Piper на компьютере",
                                    "espeak": "eSpeak", "off": "выключен"}.get(tts.get("engine"), tts.get("engine") or "без изменений")))
    for s in data.get("skills") or []:
        lines.append(f"Навык: {s.get('name') or s.get('url')}")
    if data.get("catalog"):
        lines.append(f"Каталог навыков: {data['catalog']}")
    for name, srv in ((data.get("mcp") or {}).get("servers") or {}).items():
        if srv.get("type", "stdio") == "stdio":
            warnings.append(f"MCP «{name}» запустит на компьютере программу: {' '.join([srv.get('command', '')] + list(srv.get('args') or []))}")
        else:
            lines.append(f"MCP «{name}»: {srv.get('url', '')}")
    en = ((data.get("tools") or {}).get("enabled") or {})
    if en.get("run_command"):
        warnings.append("Разрешит ассистенту выполнять команды терминала (с подтверждением)")
    if data.get("persona"):
        lines.append(f"Персонаж по умолчанию: {data['persona']}")
    need_key = bool((data.get("key") or {}).get("required")) and "${KEY}" in json.dumps(data)
    return {"name": data.get("name") or "Настройки Dewiar", "lines": lines, "warnings": warnings,
            "need_key": need_key, "key_hint": (data.get("key") or {}).get("hint", ""), "source": data.get("url", "")}


def to_patch(data, key=""):
    """Манифест → (изменения настроек, навыки для установки). Профили моделей добавляются к имеющимся, а не заменяют
    их: они идут отдельным ключом _llm_profiles, который сливает apply_patch. Адрес манифеста и код доступа
    сохраняются в разделе provision — чтобы потом обновить настройки с того же сервера."""
    validate(data)
    if (data.get("key") or {}).get("required") and "${KEY}" in json.dumps(data) and not key:
        raise ProvisionError("нужен код доступа")
    data = fill_key(copy.deepcopy(data), key.strip())
    patch = {}
    for k in SECTIONS:
        if k in data and k != "llm":
            patch[k] = data[k]
    if "llm" in data:
        patch["llm"] = {k: v for k, v in data["llm"].items() if k != "profiles"}
        patch["_llm_profiles"] = data["llm"].get("profiles") or {}
    if data.get("catalog"):
        patch["skills"] = {"catalog_url": data["catalog"]}
    if data.get("url"):
        patch["provision"] = {"url": data["url"], "name": data.get("name", ""), "key": key.strip(),
                              "applied": time.strftime("%Y-%m-%d %H:%M")}
    return patch, data.get("skills") or []


def apply_patch(settings, patch):
    """Записать в настройки изменения из to_patch(), добавив профили моделей к имеющимся."""
    patch =copy.deepcopy(patch)
    profiles = patch.pop("_llm_profiles", None)
    if profiles:
        cur = settings.get("llm", "profiles") or {}
        cur.update(profiles)
        patch.setdefault("llm", {})["profiles"] = cur
    settings.update(patch)


def packaged_manifest():
    """provision.json, вложенный в пакет, — для преднастроенных сборок (путь можно задать в DEWIAR_PROVISION)
    -> манифест или None. Испорченный файл не мешает запуску — только запись в журнал."""
    import os
    p = Path(os.environ["DEWIAR_PROVISION"]) if os.environ.get("DEWIAR_PROVISION") else DATA_DIR / "provision.json"
    if not p.exists():
        return None
    try:
        return validate(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError, ProvisionError) as e:
        log.warning("provision.json в пакете не прочитан: %s", e)
        return None


def is_fresh(snapshot):
    """Пользователь ещё ничего не настраивал (нет профилей моделей, слух по умолчанию, манифест не применялся).
    Тогда пакетный provision.json применяется сразу; уже настроенному — только с "apply_on_upgrade" и новой
    "revision" (engine._auto_provision), чтобы не затереть его настройки."""
    return not snapshot["llm"]["profiles"] and snapshot["stt"] == DEFAULTS["stt"] and not (snapshot.get("provision") or {}).get("url")
