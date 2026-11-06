"""Short-lived context for assistant turns started while the Mail page is active."""
import threading
import time

_LOCK = threading.RLock()


def data(engine):
    if not hasattr(engine, "_mail_scope"):
        engine._mail_scope = {"active": False, "seen": 0.0, "view": "all", "account_id": "",
                              "selected": None, "messages": []}
    return engine._mail_scope


def command(engine, cmd, args):
    if cmd == "mail_scope_state":
        with _LOCK:
            state = data(engine)
            active = bool(state["active"] and time.monotonic() - state["seen"] <= 30)
            return {"active": active, "selected": state["selected"] if active else None,
                    "view": state["view"] if active else ""}
    if cmd != "mail_scope_update":
        raise ValueError("Неизвестная команда режима почты")
    with _LOCK:
        state = data(engine)
        if not args.get("active"):
            state.update(active=False, seen=time.monotonic(), selected=None, messages=[])
            return {"active": False}
        selected = args.get("selected") if isinstance(args.get("selected"), dict) else None
        messages = args.get("messages") if isinstance(args.get("messages"), list) else []
        state.update(active=True, seen=time.monotonic(), view=str(args.get("view") or "all")[:32],
                     account_id=str(args.get("account_id") or "")[:40],
                     selected={key: str(selected.get(key) or "")[:400] for key in
                               ("id", "thread_id", "from", "subject", "date", "account")} if selected else None,
                     messages=[{key: str(item.get(key) or "")[:400] for key in
                                ("id", "thread_id", "from", "subject", "date", "account", "thread_count")}
                               for item in messages[:20] if isinstance(item, dict)])
        return {"active": True}


def context(engine):
    """Return headers/selection only; message bodies are loaded by explicit mail tools."""
    with _LOCK:
        state = data(engine)
        if not state["active"] or time.monotonic() - state["seen"] > 30:
            return ""
        snapshot = {key: state[key] for key in ("view", "account_id", "selected", "messages")}
    lines = ["Сейчас пользователь работает в Почте. Это команда для интеллектуальной почты."]
    if snapshot["account_id"]:
        lines.append("Выбран ящик id=" + snapshot["account_id"] + ".")
    if snapshot["selected"]:
        selected = snapshot["selected"]
        lines.append("Открытое письмо: id=" + selected["id"] + "; переписка=" + selected["thread_id"] +
                     "; от=" + selected["from"] + "; тема=" + selected["subject"] +
                     "; дата=" + selected["date"] + "; ящик=" + selected["account"] + ".")
        lines.append("Если пользователь говорит «это письмо», «ответь на него» или диктует текст ответа без другого адресата, используй открытое письмо.")
    if snapshot["messages"]:
        lines.append("Видимые строки почты (только заголовки; id сообщения и id переписки точные):")
        for item in snapshot["messages"][:15]:
            lines.append(f"- message_id={item['id']}; thread_id={item['thread_id']}; {item['from']}; «{item['subject']}»; "
                         f"{item['date']}; {item['account']}; писем в переписке: {item['thread_count']}.")
    return "\n".join(lines)


def system_note():
    return ("\n\nРЕЖИМ ПОЧТЫ: запросы о письмах выполняй инструментами mail, mail_reply и mail_importance; "
            "не отвечай по догадке и не утверждай, что прочитал письмо, не открыв его. Используй открытое письмо или точные "
            "id из контекста. Для чтения всей переписки используй mail action=thread. Если по адресу или теме найдено несколько "
            "подходящих писем и цель неясна, покажи краткий список и уточни, на какое отвечать. Продиктованный пользователем "
            "текст ответа передай в mail_reply action=prepare как body; продиктованный текст сохрани дословно, поправив только "
            "пунктуацию и явные оговорки. Если пользователь просит «проанализируй и ответь», сначала прочитай нужную переписку "
            "и составь содержательный ответ. Подготовленный текст покажи в почтовом черновике. "
            "Не отправляй без явной просьбы; действие send всегда проходит обязательное подтверждение точной версии, адресатов и текста.")
