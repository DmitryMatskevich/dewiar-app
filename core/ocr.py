"""Локальное распознавание текста на снимках экрана.

На macOS сначала используется системный Vision (не требует отдельной модели и не
отправляет изображение в сеть), затем установленный tesseract как запасной
вариант. На других системах используется только tesseract, если он есть.
"""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from platform_info import MAC

log = logging.getLogger("dewiar.ocr")


def _vision(data, languages=("ru-RU", "en-US")):
    """Распознать PNG/JPEG через macOS Vision; вернуть текст или пустую строку."""
    if not MAC:
        return ""
    try:
        import Vision
        from Foundation import NSData
        nsdata = NSData.dataWithBytes_length_(data, len(data))
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        request.setRecognitionLanguages_(list(languages))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(nsdata, {})
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            log.info("Vision OCR не выполнил запрос: %s", err)
            return ""
        out = []
        for obs in request.results() or []:
            candidate = obs.topCandidates_(1)
            if candidate:
                text = str(candidate[0].string() or "").strip()
                if text:
                    out.append(text)
        return "\n".join(out)
    except Exception as exc:  # noqa: BLE001 — отсутствие Vision не ломает запись
        log.info("Vision OCR недоступен: %s", exc)
        return ""


def _tesseract(data, languages="rus+eng"):
    exe = shutil.which("tesseract")
    if not exe:
        return ""
    try:
        with tempfile.TemporaryDirectory(prefix="dewiar-ocr-") as td:
            src = Path(td) / "frame.png"
            src.write_bytes(data)
            r = subprocess.run([exe, str(src), "stdout", "-l", languages, "--psm", "6"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode:
                log.info("tesseract OCR завершился с кодом %s: %s", r.returncode, r.stderr[-200:])
                return ""
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("tesseract OCR не выполнился: %s", exc)
        return ""


def recognize(data, languages=("ru-RU", "en-US"), max_chars=6000):
    """Локально распознать текст изображения. Ошибка OCR не отменяет сохранение кадра."""
    text = _vision(data, languages) or _tesseract(data)
    return "\n".join(x.strip() for x in text.splitlines() if x.strip())[:max_chars]

