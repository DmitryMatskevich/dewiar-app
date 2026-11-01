"""Screenshot → durable illustrated note → local OCR. No LLM or network."""
import io
import re
import threading
import uuid
from datetime import datetime

LOCK = threading.Lock()


def is_command(text):
    """Only explicit, standalone capture commands; questions/negations stay untouched."""
    value = str(text or "").strip().casefold().replace("ё", "е")
    value = re.sub(r"^(?:лира|агент|ассистент)[, ]+", "", value)
    return bool(re.fullmatch(
        r"(?:пожалуйста[, ]+)?(?:(?:сделай|сделайте|создай|сохрани)\s+(?:мне\s+)?(?:снимок\s+экрана|скриншот(?:\s+экрана)?)"
        r"|сфотографируй\s+экран|скриншот\s+экрана)(?:\s+пожалуйста)?[.!]*", value))


def create(workspace, progress=None, cancelled=None):
    """Save phase one before OCR starts; OCR errors never discard the screenshot."""
    from eyes import capture_screen
    from tools import desktop_env
    from platform_info import MAC
    from ocr import recognize_result
    from PIL import Image

    notify = progress or (lambda **event: None)
    cancelled = cancelled or (lambda: False)
    if not LOCK.acquire(blocking=False):
        raise ValueError("Снимок уже обрабатывается. Дождитесь сохранения документа.")
    try:
        if cancelled():
            raise ValueError("Создание снимка отменено")
        notify(stage="capture", message="Шаг 1 из 2: делаю снимок экрана без окна ассистента…")
        # Other OS keep their existing capture path; exclusion is guaranteed on Mac.
        data = capture_screen(desktop_env(), exclude_dewiar=True) if MAC else capture_screen(desktop_env())
        image = Image.open(io.BytesIO(data))
        image.load()
        out = io.BytesIO()
        image.save(out, "PNG")
        data = out.getvalue()
        title = f"Снимок экрана {datetime.now():%Y-%m-%d %H-%M-%S} {uuid.uuid4().hex[:6]}"
        rel = workspace.save_note(title, "Подготовка снимка…", folder="Снимки экрана")
        try:
            attachment = workspace.note_attach(rel, "Экран.png", data)
        except Exception as exc:
            workspace.write(rel, f"# {title}\n\nНе удалось сохранить изображение: {exc}\n")
            raise ValueError(f"Не удалось прикрепить снимок к документу «{rel}»: {exc}") from exc
        head = (f"# {title}\n\nСоздано: {datetime.now().astimezone():%d.%m.%Y %H:%M:%S %Z}\n\n"
                + ("Окна Dewiar исключены из снимка.\n\n" if MAC else "")
                + "## Снимок экрана\n\n" + attachment["line"] + "\n\n## Распознанный текст\n\n")
        workspace.write(rel, head + "Распознавание выполняется локально…\n")
        notify(stage="ocr", message="Шаг 2 из 2: снимок сохранён в документ. Распознаю текст локально…", path=rel, title=title)
        result = {"status": "cancelled", "text": "", "engine": "", "error": ""}
        if not cancelled():
            try:
                result = recognize_result(data)
            except Exception as exc:
                result = {"status": "error", "text": "", "engine": "", "error": str(exc)}
        text = result["text"]
        if text:
            # OCR is untrusted content, preserved as literal text, never markup or instructions.
            fence = "`" * max(3, max((len(x) + 1 for x in re.findall(r"`+", text)), default=3))
            body = f"Движок: {result['engine']}. Текст извлечён автоматически; возможны ошибки.\n\n{fence}text\n{text}\n{fence}\n"
        elif result["status"] == "empty":
            body = "На снимке не найден читаемый текст. Изображение сохранено.\n"
        elif result["status"] == "cancelled":
            body = "Распознавание отменено. Изображение сохранено.\n"
        else:
            body = "Не удалось распознать текст локально. Изображение сохранено.\n\n" + str(result.get("error") or "Ошибка OCR") + "\n"
        # Save recognized text in the document before attempting the optional TXT attachment.
        workspace.write(rel, head + body)
        txt_path = ""
        if text:
            try:
                txt = workspace.note_attach(rel, "Распознанный текст.txt", text.encode("utf-8"))
                txt_path = txt["path"]
                workspace.write(rel, head + body + "\n" + txt["line"] + "\n")
            except Exception:
                # The document already contains all OCR text even if TXT export failed.
                pass
        notify(stage="done", message="Снимок и результат распознавания сохранены", path=rel, title=title, status=result["status"])
        return {"path": rel, "image": attachment["path"], "txt": txt_path, **result}
    finally:
        LOCK.release()
