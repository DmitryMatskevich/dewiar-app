"""Подготовка локализации видео для OpenMontage.

Модуль не монтирует и не публикует видео: сохраняет перевод, целевые субтитры,
сценарий озвучки и manifest проекта. Исходная расшифровка остаётся неизменной.
"""
import json
import re
from pathlib import Path

VERSION = 'video-translation-1'


def _prompt_full(source_language, target_language, title, text):
    return f"""Переведи расшифровку видео с {source_language} на {target_language}.
Сохрани смысл, технические термины, имена, числа и структуру абзацев. Не добавляй факты,
которых нет в исходнике. Верни только литературный перевод без пояснений и заголовка.

Название видео: {title or 'неизвестно'}
ИСХОДНЫЙ ТЕКСТ:
{text}
"""


def _prompt_segments(source_language, target_language, rows):
    payload = json.dumps([{'id': i, 'text': row[2]} for i, row in enumerate(rows)], ensure_ascii=False)
    return f"""Переведи короткие реплики с {source_language} на {target_language}.
Верни только JSON-массив объектов вида {{"id": число, "text": "перевод"}}.
Сохрани порядок и все id, не добавляй комментарии, markdown и новые реплики.
Данные: {payload}
"""


def _json_array(text):
    value = str(text or '').strip()
    value = re.sub(r'^```(?:json)?\s*|\s*```$', '', value, flags=re.I | re.S).strip()
    start, end = value.find('['), value.rfind(']')
    if start < 0 or end <= start:
        raise ValueError('модель не вернула JSON-массив субтитров')
    data = json.loads(value[start:end + 1])
    if not isinstance(data, list):
        raise ValueError('формат перевода субтитров не является массивом')
    out = {}
    for row in data:
        if isinstance(row, dict) and isinstance(row.get('id'), int) and str(row.get('text') or '').strip():
            out[row['id']] = re.sub(r'\s+', ' ', str(row['text'])).strip()
    return out


def _srt_time(value):
    ms = max(0, round(float(value) * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def _srt(rows):
    return ''.join(f'{i}\n{_srt_time(a)} --> {_srt_time(b)}\n{text}\n\n' for i, (a, b, text) in enumerate(rows, 1))


def ensure_translation(directory, source_language, target_language, generate, title='', source='', notify=lambda _: None, voice=None):
    directory = Path(directory)
    clean = directory / 'transcript.clean.md'
    segments_path = directory / 'segments.json'
    if not clean.is_file() or not segments_path.is_file():
        raise FileNotFoundError('Для перевода нужна чистовая расшифровка и segments.json')
    try:
        segments = [(float(a), float(b), str(text).strip()) for a, b, text in json.loads(segments_path.read_text(encoding='utf-8')) if str(text).strip()]
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('Не удалось прочитать сегменты исходной речи') from exc
    source_language = source_language or 'auto'
    target_language = target_language or 'ru'
    source_text = clean.read_text(encoding='utf-8')
    notify(f'Перевожу текст: {source_language} → {target_language}…')
    translated = str(generate(_prompt_full(source_language, target_language, title, source_text)) or '').strip()
    if not translated:
        raise ValueError('Модель не вернула перевод текста')
    translated_segments = []
    for offset in range(0, len(segments), 32):
        batch = segments[offset:offset + 32]
        notify(f'Готовлю субтитры: {min(offset + len(batch), len(segments))}/{len(segments)}')
        try:
            mapped = _json_array(generate(_prompt_segments(source_language, target_language, batch)))
            if any(i not in mapped for i in range(len(batch))):
                raise ValueError('модель пропустила сегмент субтитров')
            translated_segments.extend((a, b, mapped[i]) for i, (a, b, _) in enumerate(batch))
        except Exception as exc:
            # Не выдаём ложный перевод: исходные сегменты сохраняем, а в manifest отмечаем,
            # что целевые субтитры требуют повторной попытки/ручной проверки.
            raise ValueError(f'Не удалось подготовить целевые субтитры: {exc}') from exc
    (directory / 'translation.md').write_text(f'# Перевод видео\n\n{translated.rstrip()}\n', encoding='utf-8')
    script = ('# Сценарий озвучки\n\n'
              f'**Язык оригинала:** {source_language}\n\n'
              f'**Язык озвучки:** {target_language}\n\n{translated.rstrip()}\n')
    (directory / 'dubbing-script.md').write_text(script, encoding='utf-8')
    (directory / 'translation.srt').write_text(_srt(translated_segments), encoding='utf-8')
    manifest = {
        'version': VERSION, 'status': 'prepared', 'title': title or '', 'source': source or '',
        'source_language': source_language, 'target_language': target_language,
        'voice': voice or '',
        'source_transcript': 'transcript.clean.md', 'translation': 'translation.md',
        'target_subtitles': 'translation.srt', 'dubbing_script': 'dubbing-script.md',
        'voice_mode': 'pending_user_choice', 'screen_text_review': 'pending',
        'audio_qa': 'pending', 'render': 'not_started', 'publication': 'not_requested',
    }
    (directory / 'openmontage.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    info = dict(version=VERSION, source_language=source_language, target_language=target_language,
                translation='translation.md', subtitles='translation.srt', script='dubbing-script.md',
                manifest='openmontage.json', segments=len(translated_segments), status='prepared')
    (directory / 'translation.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return info
