"""Общее резюме расшифровки. Не изменяет исходный текст и аудио."""
import hashlib
import json
import re
from pathlib import Path

VERSION = 'ru-summary-2'
SUMMARY_FILE = 'transcript.summary.md'
META_FILE = 'summary.json'


def _fallback(title, text):
    """Безопасный локальный резерв: не придумывает спикеров и выводы."""
    body = re.sub(r'\s+', ' ', str(text or '')).strip()
    sentences = re.split(r'(?<=[.!?…])\s+', body)
    preview = ' '.join(sentences[:4]).strip()
    if len(preview) > 900:
        preview = preview[:897].rsplit(' ', 1)[0] + '…'
    return (f"# Общее резюме\n\n"
            f"## О чём ролик\n\n{preview or 'Содержание не удалось извлечь из расшифровки.'}\n\n"
            f"## Кто выступает\n\nВ расшифровке нет надёжно выделенного имени выступающего; требуется проверка по аудио или описанию ролика.\n\n"
            f"## Основные идеи\n\n- Выделите ключевые тезисы по полной расшифровке.\n\n"
            f"## Заключение и вывод\n\nАвтоматический локальный резерв не делает выводов, которых нет в тексте.\n")


def _source_text(text, limit=48000):
    text = str(text or '')
    if len(text) <= limit:
        return text
    # Для длинных роликов сохраняем начало, середину и финал — там обычно есть тема,
    # представление спикеров и заключение.
    part = limit // 3
    return text[:part] + "\n\n[...середина расшифровки сокращена...]\n\n" + text[len(text)//2-part//2:len(text)//2+part//2] + "\n\n[...начало заключения...]\n\n" + text[-part:]


def _prompt(title, source, text):
    return f"""Ты готовишь редакторское общее резюме русскоязычного видео по расшифровке.
Не выдумывай имена, должности, факты или выводы. Если выступающий не назван явно, напиши, что имя не установлено.
Отделяй сказанное в ролике от своих предположений. Пиши кратко, конкретно и на русском.

Верни только Markdown со строго четырьмя разделами:
## О чём ролик
Один связный абзац.
## Кто выступает
Имена и роли только если они подтверждены расшифровкой; иначе честно укажи неопределённость.
## Основные идеи
5–10 маркированных тезисов, без повторов и общих слов.
## Заключение и вывод
Кратко передай итог автора/спикера и отдельно обозначь, если собственного явного вывода в ролике нет.

Название: {title or 'неизвестно'}
Источник: {source or 'локальный файл'}

РАСШИФРОВКА:
{_source_text(text)}
"""


def _normalize(value):
    value = str(value or '').replace('\x00', '').strip()
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value


def ensure_summary(directory, title='', source='', generate=None, notify=lambda message: None, force=False):
    """Создаёт summary Markdown и JSON-аудит; повторный вызов идемпотентен."""
    directory = Path(directory)
    clean = directory / 'transcript.clean.md'
    out = directory / SUMMARY_FILE
    meta_path = directory / META_FILE
    if not clean.is_file():
        raise FileNotFoundError('Нет чистовой расшифровки для общего резюме')
    text = clean.read_text(encoding='utf-8')
    fingerprint = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if not force and out.is_file() and meta_path.is_file():
        try:
            old = json.loads(meta_path.read_text(encoding='utf-8'))
            if old.get('version') == VERSION and old.get('source_sha256') == fingerprint:
                return dict(old, path=SUMMARY_FILE, cached=True)
        except (OSError, ValueError):
            pass
    notify('Готовлю общее резюме: тема, выступающие, идеи и вывод…')
    generation_error = ''
    if generate:
        try:
            result = _normalize(generate(_prompt(title, source, text)))
            if not result:
                raise ValueError('модель не вернула содержательное резюме')
            if not result.startswith('#'):
                result = '# Общее резюме\n\n' + result
            method = 'llm'
        except Exception as exc:  # резюме не должно уничтожить уже готовую расшифровку
            if 'остановили' in str(exc).lower():
                raise
            generation_error = str(exc)[:300]
            notify('Модель резюме недоступна — сохраняю локальный резерв без выдуманных фактов.')
            result = _fallback(title, text)
            method = 'local-fallback'
    else:
        result = _fallback(title, text)
        method = 'local-fallback'
    tmp = out.with_suffix(out.suffix + '.tmp')
    tmp.write_text(result.rstrip() + '\n', encoding='utf-8')
    tmp.replace(out)
    meta = dict(version=VERSION, source_sha256=fingerprint, method=method,
                title=title or '', source=source or '', characters=len(result),
                path=SUMMARY_FILE)
    if generation_error:
        meta['generation_error'] = generation_error
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return meta
