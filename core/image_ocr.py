"""OCR of an explicitly attached image, locally and without a model turn."""
import base64
import binascii
import io
import threading
import warnings

MAX_BYTES = 10 * 1024 * 1024  # base64 + JSON fits the bridge's 16 MiB message limit
MAX_PIXELS = 36_000_000
_LOCK = threading.Lock()


def extract(data_url):
    from PIL import Image, ImageOps
    from ocr import recognize_result

    if not isinstance(data_url, str) or len(data_url) > (MAX_BYTES * 4 // 3 + 256):
        raise ValueError("Для OCR выберите картинку размером до 10 МБ.")
    header, separator, payload = data_url.partition(',')
    if not separator or not header.startswith('data:image/') or not header.endswith(';base64'):
        raise ValueError("Передайте изображение из вложений.")
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Не удалось прочитать данные картинки.") from exc
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Для OCR выберите непустую картинку размером до 10 МБ.")
    if not _LOCK.acquire(blocking=False):
        raise ValueError("Другая картинка уже распознаётся. Дождитесь результата.")
    try:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.width * image.height > MAX_PIXELS:
                        raise ValueError("Картинка слишком большая для OCR: максимум 36 мегапикселей.")
                    image.load()
                    image = ImageOps.exif_transpose(image)
                    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                        rgba = image.convert('RGBA')
                        image = Image.new('RGB', rgba.size, 'white')
                        image.paste(rgba, mask=rgba.getchannel('A'))
                    else:
                        image = image.convert('RGB')
                    output = io.BytesIO()
                    image.save(output, 'PNG')
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("Картинка слишком большая для OCR.") from exc
        except (OSError, SyntaxError) as exc:
            raise ValueError("Не удалось открыть картинку для OCR.") from exc
        return recognize_result(output.getvalue())
    finally:
        _LOCK.release()
