"""Единая подпись реплики для .txt, Markdown, запросов к модели и проигрывателя."""
BASE = {"them": "Собеседник", "me": "Вы", "screen": "Экран"}
LLM_BASE = {"them": "Собеседник", "me": "Вы", "screen": "Экран (OCR)"}


def label(line, base=BASE):
    """Подпись участника (имя или «Участник N»), иначе прежняя «Собеседник» / «Вы» / «Экран»."""
    if line.get("who") == "screen":
        return base["screen"]
    text = str(line.get("label") or "").strip()
    return text or base.get(line.get("who"), str(line.get("who") or ""))
