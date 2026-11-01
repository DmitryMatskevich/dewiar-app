"""Локальное распознавание текста на снимках экрана.

На macOS сначала используется системный Vision (не требует отдельной модели и не
отправляет изображение в сеть), затем установленный tesseract как запасной
вариант. На других системах используется только tesseract, если он есть.
"""
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from platform_info import MAC

log = logging.getLogger("dewiar.ocr")


def _vision_worker(data, languages, details):
    """Keep native Vision out of the long-lived frozen core's thread pool.

    A stuck native request must time out so the existing Tesseract fallback can
    finish the note, rather than leaving it indefinitely at 'OCR in progress'.
    """
    import json
    result = subprocess.run([sys.executable, "--ocr-worker", json.dumps(list(languages)),
                             "details" if details else "text"], input=data,
                            capture_output=True, timeout=25)
    if result.returncode:
        raise RuntimeError("Процесс Vision завершился с ошибкой")
    reply = json.loads(result.stdout)
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error") or "Vision не выполнил запрос")
    return reply["result"]


def worker_main(args):
    """Private .app entry: image on stdin, one JSON response on stdout; no model."""
    import json
    try:
        # PyInstaller windowed builds may set sys.stdin/stdout to None even when
        # the parent supplied pipes. Use their actual descriptors instead.
        with os.fdopen(os.dup(0), "rb") as source:
            data = source.read()
        result = _vision(data, json.loads(args[0]), strict=True, details=args[1] == "details")
        reply = {"ok": True, "result": result}
    except Exception as exc:
        reply = {"ok": False, "error": str(exc)}
    with os.fdopen(os.dup(1), "wb") as destination:
        destination.write(json.dumps(reply, ensure_ascii=False).encode("utf-8"))


def _vision(data, languages=("ru-RU", "en-US"), strict=False, details=False):
    """Распознать PNG/JPEG через macOS Vision; вернуть текст или пустую строку."""
    if not MAC:
        return ""
    try:
        if getattr(sys, "frozen", False) and "--ocr-worker" not in sys.argv:
            return _vision_worker(data, languages, details)
        import Vision
        from Foundation import NSData
        nsdata = NSData.dataWithBytes_length_(data, len(data))
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        # Reuse LANMASTER/OpenMontage frame OCR: preserve article names, identifiers
        # and mixed RU/EN text instead of letting spelling correction rewrite it.
        request.setUsesLanguageCorrection_(False)
        request.setRecognitionLanguages_(list(languages))
        if strict:
            supported, error = request.supportedRecognitionLanguagesAndReturnError_(None)
            missing = [language for language in languages if language not in (supported or [])]
            if error or missing:
                raise RuntimeError("Vision не поддерживает языки: " + ", ".join(missing))
        # A bridged Python {} raises 'key does not exist' on current macOS.
        # nil is the documented empty options dictionary and works on older Macs too.
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(nsdata, None)
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            if strict:
                raise RuntimeError(str(err or "Vision не выполнил запрос"))
            log.info("Vision OCR не выполнил запрос: %s", err)
            return ""
        out, lines = [], []
        for obs in request.results() or []:
            candidate = obs.topCandidates_(1)
            if candidate:
                text = str(candidate[0].string() or "").strip()
                if text:
                    out.append(text)
                    box = obs.boundingBox()
                    lines.append({"text": text, "confidence": float(candidate[0].confidence()),
                                  "box": [float(box.origin.x), float(box.origin.y),
                                          float(box.size.width), float(box.size.height)]})
        text = "\n".join(out)
        return {"text": text, "lines": lines} if details else text
    except Exception as exc:  # noqa: BLE001 — отсутствие Vision не ломает запись
        if strict:
            raise RuntimeError("Vision: " + str(exc)) from exc
        log.info("Vision OCR недоступен: %s", exc)
        return ""


def tessdata_dir(languages="rus+eng"):
    """Use Dewiar's own RU/EN assets, never a hardcoded old project directory."""
    from settings import MODELS_DIR, DATA_DIR
    candidates = [os.environ.get("DEWIAR_TESSDATA_DIR"), MODELS_DIR / "ocr/tessdata", DATA_DIR / "ocr/tessdata"]
    for path in candidates:
        if path and all((Path(path) / (language + ".traineddata")).is_file() for language in languages.split("+")):
            return str(path)
    return ""


def _tesseract(data, languages="rus+eng", strict=False, psm=6):
    exe = shutil.which("tesseract")
    if not exe:
        if strict:
            raise RuntimeError("Tesseract не установлен")
        return ""
    try:
        with tempfile.TemporaryDirectory(prefix="dewiar-ocr-") as td:
            src = Path(td) / "frame.png"
            src.write_bytes(data)
            models = tessdata_dir(languages)
            command = [exe, str(src), "stdout"] + (["--tessdata-dir", models] if models else [])
            r = subprocess.run(command + ["-l", languages, "--psm", str(psm)],
                               capture_output=True, text=True, timeout=30)
            if strict and "Failed loading language" in r.stderr:
                raise RuntimeError("Tesseract: не установлен русский языковой пакет rus. " + r.stderr.strip()[:200])
            if r.returncode:
                if strict:
                    raise RuntimeError("Tesseract: " + r.stderr.strip()[-200:])
                log.info("tesseract OCR завершился с кодом %s: %s", r.returncode, r.stderr[-200:])
                return ""
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        if strict:
            raise RuntimeError("Tesseract: " + str(exc)) from exc
        log.info("tesseract OCR не выполнился: %s", exc)
        return ""


def recognize_result(data, languages=("ru-RU", "en-US")):
    """Full local OCR result: distinguish an empty screen from failed engines."""
    engines = ([("macOS Vision", lambda: _vision(data, languages, strict=True, details=True))] if MAC else [])
    if shutil.which("tesseract"):
        lang = "+".join({"ru-RU": "rus", "en-US": "eng"}.get(x, x) for x in languages)
        engines.append(("Tesseract", lambda: _tesseract(data, languages=lang, strict=True, psm=3)))
    errors, empty = [], ""
    for name, run in engines:
        try:
            raw = run()
            text = "\n".join(line.strip() for line in (raw["text"] if isinstance(raw, dict) else raw).splitlines() if line.strip())
            if text:
                return {"status": "done", "text": text, "engine": name, "error": "",
                        "lines": raw.get("lines", []) if isinstance(raw, dict) else []}
            empty = name
        except Exception as exc:
            log.info("OCR снимка: %s", exc)
            errors.append(str(exc))
    if empty:
        return {"status": "empty", "text": "", "engine": empty, "error": ""}
    return {"status": "error", "text": "", "engine": "", "error": "; ".join(errors) or "Локальный движок OCR не установлен"}


def recognize(data, languages=("ru-RU", "en-US"), max_chars=6000):
    """Локально распознать текст изображения. Ошибка OCR не отменяет сохранение кадра."""
    text = _vision(data, languages) or _tesseract(data)
    return "\n".join(x.strip() for x in text.splitlines() if x.strip())[:max_chars]

