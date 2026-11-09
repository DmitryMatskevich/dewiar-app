"""Extract structured contacts from images using the app's local OCR."""


def _contacts(recognized):
    from contact_extraction import extract_contacts_from_text, has_contacts
    if recognized.get("status") not in {"done", "empty"}:
        return {"status": recognized.get("status", "error"), "contacts": {"email": [], "phone": [], "website": []},
                "error": recognized.get("error") or "Не удалось прочитать изображение.", "engine": recognized.get("engine", "")}
    contacts = extract_contacts_from_text(recognized.get("text", ""))
    if not has_contacts(contacts):
        return {"status": "empty", "contacts": contacts, "error": "Контактные данные на изображении не обнаружены.", "engine": recognized.get("engine", "")}
    return {"status": "done", "contacts": contacts, "error": "", "engine": recognized.get("engine", "")}


def extract_bytes(data):
    """Shared contact extractor for trusted local image bytes, including sampled video frames."""
    import subprocess, sys
    from pathlib import Path
    from ocr import recognize_result
    result = recognize_result(data, ("ru-RU", "en-US"))
    if result.get("status") == "error":
        app = Path("/Applications/Dewiar.app/Contents/MacOS/Dewiar")
        if not getattr(sys, "frozen", False) and app.is_file():
            run = subprocess.run([str(app), "--ocr-worker", '["ru-RU","en-US"]', "details"], input=data, capture_output=True, timeout=30)
            if run.returncode == 0:
                try:
                    import json
                    raw = json.loads(run.stdout)
                    if raw.get("ok"):
                        value = raw["result"]
                        result = {"status": "done" if value.get("text") else "empty", "text": value.get("text", ""), "engine": "macOS Vision", "error": ""}
                except (ValueError, TypeError):
                    pass
    return _contacts(result)


def extract(image):
    from image_ocr import extract as recognize_image
    return _contacts(recognize_image(image))
