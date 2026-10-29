"""«Безопасность» в студии (0.38.98): стоп-кран, схема «куда идут ваши данные», замки и журнал — одним ответом ядра.

state(eng)        — всё для страницы: стоп-кран, схема по текущим настройкам, замки, журнал за сегодня;
set_(eng, patch)  — изменить: {"local_only": bool} | {"lock": "camera"|"screen"|"files"|"mic", "value": …}.

Стоп-кран (privacy.local_only) сам по себе только запрещает соединения наружу (core/egress.py). Чтобы ассистент при этом
продолжал думать, set_ переключает «Модели» на свою модель (Ollama, LM Studio, vLLM на этом компьютере или в домашней сети) и
помнит прежнюю в privacy.prev_llm; выключили — возвращает её. Слух и голос в интернете на это время подменяются своими
(engine_audio.apply_engines). Синхронизация, «Близкие», боты, обновления и пульс — на паузе, каждый у себя.

Замки — это не новые правила, а понятные имена для существующих:
- камера:   look_through_camera — «Нельзя» (выключен) | «Спрашивать» (включён, с вопросом);
- экран:    look_at_screen и save_screenshot — «Нельзя» | «Спрашивать» | «Можно»;
- файлы:    privacy.files — «all» (как всегда: удаление в корзину с «да», системное нельзя) | «folder» (удалять и отправлять
            файлы — только из папки ассистента, команды терминала выключены — tools.Tools.policy/guard);
- микрофон: privacy.mic — «name» (ждёт имя) | «button» (только кнопка: при запуске микрофон выключен, «говорить» — одна фраза)."""
from urllib.parse import urlparse

import egress

CAMERA = ("look_through_camera",)
SCREEN = ("look_at_screen", "save_screenshot")


def _host(url):
    u = str(url or "")
    try:
        return (urlparse(u if "://" in u else "http://" + u).hostname or "").lower()
    except ValueError:
        return ""


def _where(url, cloud=False):
    """-> here (этот компьютер) | home (домашняя сеть) | dewiar (серверы Dewiar) | service (чужой сервис) | none."""
    if cloud:
        return "dewiar"
    h = _host(url)
    if not h:
        return "none"
    if h in ("localhost", "::1") or h.startswith("127.") or h.endswith(".localhost"):
        return "here"
    if egress.local_host(h):
        return "home"
    if "dewiar" in h:
        return "dewiar"
    return "service"


def local_profiles(s):
    """Свои модели: профили раздела «Модели» на этом компьютере или в домашней сети (не облако Dewiar). -> [(id, профиль)]."""
    from cloud import is_cloud
    out = []
    for pid, p in (s.get("llm", "profiles") or {}).items():
        if p.get("kind") not in ("codex_subscription", "claude_subscription") and not is_cloud(p) and _where(p.get("base_url")) in ("here", "home"):
            out.append((pid, p))
    out.sort(key=lambda x: 0 if _where(x[1].get("base_url")) == "here" else 1)
    return out


def _brain(s):
    from cloud import is_cloud
    p = s.active_llm() or {}
    w = "service" if p.get("kind") in ("codex_subscription", "claude_subscription") else (_where(p.get("base_url"), is_cloud(p)) if p else "none")
    name = p.get("name") or ""
    if w == "dewiar":
        name = "Dewiar Облако"
    elif w == "service":
        name = p.get("name") or _host(p.get("base_url"))
    return {"where": w, "name": name, "host": _host(p.get("base_url")) if w in ("service",) else ""}


def _stt(s, lo):
    st = s.get("stt") or {}
    if st.get("engine") == "remote" and (st.get("remote") or {}).get("base_url"):
        w = _where(st["remote"]["base_url"])
        if lo and w in ("dewiar", "service"):
            return {"where": "here", "name": "свой (на время стоп-крана)"}
        return {"where": w, "name": "сервер Dewiar" if w == "dewiar" else _host(st["remote"]["base_url"])}
    return {"where": "here", "name": "GigaAM" if st.get("engine") == "gigaam" else "Vosk"}


def _tts(s, lo):
    tt = s.get("tts") or {}
    e = tt.get("engine")
    if tt.get("text_only") or e == "off":
        return {"where": "off", "name": "только текст"}
    if e == "elevenlabs" and (tt.get("eleven") or {}).get("api_key"):
        return {"where": "here" if lo else "service", "name": "свой (на время стоп-крана)" if lo else "ElevenLabs"}
    if e == "remote" and (tt.get("remote") or {}).get("base_url"):
        w = _where(tt["remote"]["base_url"])
        if lo and w in ("dewiar", "service"):
            return {"where": "here", "name": "свой (на время стоп-крана)"}
        return {"where": w, "name": "сервер Dewiar" if w == "dewiar" else _host(tt["remote"]["base_url"])}
    return {"where": "here", "name": {"piper": "Piper", "espeak": "eSpeak"}.get(e, "Vosk TTS")}


def locks(s):
    t = s.get("tools") or {}
    en, cf = t.get("enabled") or {}, t.get("confirm") or {}

    def lvl(names, default_confirm):
        n = names[0]
        if en.get(n, True) is False:
            return "off"
        return "ask" if cf.get(n, default_confirm) else "on"
    pv = s.get("privacy") or {}
    cam = lvl(CAMERA, True)
    return {"camera": "off" if cam == "off" else "ask", "screen": lvl(SCREEN, False),
            "files": "folder" if pv.get("files") == "folder" else "all",
            "mic": "button" if pv.get("mic") == "button" else "name"}


def state(eng):
    s = eng.s
    lo = bool(s.get("privacy", "local_only"))
    logged = bool(eng.account.logged_in())
    brain = _brain(s)
    own = local_profiles(s)
    sync_on = bool(s.get("sync", "enabled")) and logged
    tg = bool(getattr(eng, "telegram", None) and eng.telegram.paired())
    mx = bool(getattr(eng, "max", None) and eng.max.paired())
    flows = {
        "brain": brain,
        "stt": _stt(s, lo),
        "tts": _tts(s, lo),
        "sync": {"on": sync_on, "e2e": bool(s.get("sync", "e2e")), "paused": lo and sync_on},
        "close": {"on": logged, "paused": lo and logged},
        "bots": {"on": tg or mx, "names": [n for n, v in (("Telegram", tg), ("MAX", mx)) if v], "paused": lo and (tg or mx)},
        "web": {"via_dewiar": s.get("net", "web_via") is not False, "paused": lo},
        "pulse": {"on": (s.get("report") or {}).get("pulse", True) is not False, "paused": lo},
    }
    return {"local_only": lo, "own": [{"id": i, "name": p.get("name") or i} for i, p in own],
            "flows": flows, "locks": locks(s), "journal": egress.journal(),
            "workspace": str(getattr(getattr(eng, "workspace", None), "root", "") or "")}


class SecurityError(Exception):
    pass


def set_(eng, a):
    """Изменить стоп-кран или замок. -> {state, note}: note — что сделано словами (для тоста)."""
    s = eng.s
    note = ""
    if "local_only" in a:
        on = bool(a["local_only"])
        pv = s.get("privacy") or {}
        if on and not pv.get("local_only"):
            act = s.get("llm", "active") or ""
            own = local_profiles(s)
            if own and act not in [i for i, _ in own]:
                s.update({"privacy": {"local_only": True, "prev_llm": act}, "llm": {"active": own[0][0], "auto_from": ""}})
                note = f"Наружу ничего не уходит. Думаю своей моделью «{own[0][1].get('name') or own[0][0]}»."
            else:
                s.update({"privacy": {"local_only": True, "prev_llm": ""}})
                note = "Наружу ничего не уходит." + ("" if own else " Своей модели нет — отвечать как собеседник не смогу, "
                                                      "пока не подключите её в разделе «Модели» или не выключите стоп-кран.")
        elif not on and pv.get("local_only"):
            prev = pv.get("prev_llm") or ""
            patch = {"privacy": {"local_only": False, "prev_llm": ""}}
            if prev and prev in (s.get("llm", "profiles") or {}) and (s.get("llm", "active") or "") in [i for i, _ in local_profiles(s)]:
                patch["llm"] = {"active": prev}
            s.update(patch)
            note = "Стоп-кран выключен — всё работает как раньше."
        egress.set_local_only(bool(s.get("privacy", "local_only")))
    if "lock" in a:
        k, v = str(a["lock"]), str(a.get("value") or "")
        if k == "camera" and v in ("off", "ask"):
            s.update({"tools": {"enabled": {n: v != "off" for n in CAMERA}, "confirm": {n: True for n in CAMERA}}})
        elif k == "screen" and v in ("off", "ask", "on"):
            s.update({"tools": {"enabled": {n: v != "off" for n in SCREEN}, "confirm": {n: v == "ask" for n in SCREEN}}})
        elif k == "files" and v in ("folder", "all"):
            s.update({"privacy": {"files": v}})
        elif k == "mic" and v in ("name", "button"):
            s.update({"privacy": {"mic": v}})
            if hasattr(eng, "set_mute"):
                eng.set_mute(v == "button")
        else:
            raise SecurityError("нет такого замка")
    return {"state": state(eng), "note": note}
