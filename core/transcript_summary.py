"""Краткое резюме и подробный тематический конспект по всей расшифровке."""
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

VERSION = 'ru-summary-7'
SUMMARY_FILE = 'transcript.summary.md'
TOPICS_FILE = 'transcript.topics.md'
META_FILE = 'summary.json'
CHUNK_CHARS = 20_000
CHUNK_OVERLAP = 1_000


def _split_text(text, limit=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    """Разбивает полный текст с небольшим перекрытием; ни один участок не пропускается."""
    text = str(text or '').strip()
    if not text:
        return []
    out, start, n = [], 0, len(text)
    while start < n:
        end = min(n, start + limit)
        if end < n:
            # Сохраняем целые абзацы/предложения, если граница рядом с лимитом.
            floor = start + int(limit * .72)
            candidates = [text.rfind('\n\n', floor, end), text.rfind('\n', floor, end),
                          text.rfind('. ', floor, end), text.rfind('? ', floor, end),
                          text.rfind('! ', floor, end)]
            boundary = max(candidates)
            if boundary > floor:
                end = boundary + (2 if text[boundary:boundary + 2] in ('\n\n', '. ', '? ', '! ') else 1)
        chunk = text[start:end].strip()
        if chunk:
            out.append(chunk)
        if end >= n:
            break
        next_start = max(start + 1, end - overlap)
        # Align the overlap with a paragraph boundary where practical.
        paragraph = text.find('\n\n', next_start, min(end, next_start + 800))
        if paragraph >= 0:
            next_start = paragraph + 2
        start = next_start
    return out


def _chunk_prompt(title, source, index, total, chunk):
    return f"""Проанализируй фрагмент {index} из {total} полной расшифровки. Это часть длинной записи; не делай общий вывод обо всём видео.
Извлеки содержание максимально полно. Разделяй самостоятельные темы и подтемы. Для каждой сохрани главную мысль, объяснения, аргументы, примеры, цифры, названия технологий, ограничения, рекомендации и вопросы. Не своди разные идеи к общим формулировкам и не выбрасывай детали только ради краткости.
Не перечисляй выступающих, не приписывай мысли людям. Не добавляй фактов из внешних знаний. Если расшифровка неоднозначна или слово могло быть распознано неверно, пометь это как неопределённость.
Верни Markdown: заголовки по реальным темам и подробные пункты под ними. Каждый пункт оформи так: «- тезис. Опора: «короткая дословная фраза из этого места»». В цитате должно быть примерно 6–15 непрерывных слов именно из предоставленного текста; не исправляй распознанные слова и не соединяй разрозненные места. На каждый пункт нужна своя цитата. Таймкоды не указывай: я добавлю их автоматически по точному месту цитаты. Не добавляй числа, названия технологий или выводы без опоры на цитату. Если термин сомнительно распознан, сохрани его в цитате и отметь неопределённость. Ориентир — 600–1000 слов, сокращай повторы, но не темы.

Название: {title or 'неизвестно'}
Источник: {source or 'локальный файл'}

ФРАГМЕНТ {index}/{total}:
{chunk}
"""


def _final_prompt(title, source, notes, kind):
    if kind == 'short':
        return f"""На основе заметок по всем частям расшифровки составь содержательное краткое резюме всей записи на русском языке.
Запись длинная: не своди её к общему описанию или нескольким универсальным тезисам и обязательно пройди заметки от первого до последнего фрагмента. Отрази основные самостоятельные темы, включая темы, которые возникли лишь в одной части; объединяй только повторы. Сохрани порядок появления тем, основные идеи, существенные аргументы, ключевые технические детали, примеры, числа, ограничения, решения и открытые вопросы. Не перечисляй выступающих и не связывай содержание с именами. Не добавляй внешние сведения.
Оформи заголовком и тематическими пунктами. На каждый фрагмент заметок дай от одного до трёх пунктов: объедини близкие детали в один тезис, но не смешивай самостоятельные темы. Включи хотя бы один содержательный пункт из каждого фрагмента. Для каждого пункта сохрани таймкод и короткую дословную цитату из заметок, копируя их без изменений. Подробности и дополнительную доказательную основу оставь в тематическом конспекте. Верни только Markdown.

Название: {title or 'неизвестно'}
Источник: {source or 'локальный файл'}

ЗАМЕТКИ ПО ВСЕМ ЧАСТЯМ:
{notes}
"""
    return f"""Собери подробный тематический конспект всей записи на русском языке по заметкам, извлечённым последовательно из каждого участка полной расшифровки.
Требования:
- Сохрани все самостоятельные крупные темы и важные подтемы, включая краткие темы, которые обсуждались только в одном фрагменте. Не ограничивай число тем фиксированным списком.
- Располагай темы в порядке появления в записи. Объединяй повторы, но сохраняй различия, аргументы, примеры, цифры, технические детали, ограничения, вопросы и выводы.
- Не перечисляй выступающих и не связывай тезисы с именами. Этот документ должен описывать содержание записи без диаризации.
- Отделяй факты, которые прямо следуют из заметок, от неопределённости распознавания. Не дополняй обсуждение общеизвестными сведениями. Для каждого тезиса сохраняй точную цитату и таймкод из заметок; цитаты и таймкоды копируй без изменений. Не добавляй числа и названия без подтверждающей цитаты.
- Заверши отдельными разделами «Практические выводы и решения» и «Открытые вопросы», включай их только если такие пункты действительно есть в материалах.
- Пиши достаточно подробно, чтобы по конспекту можно было восстановить ход и содержание конференции. Не сокращай до 5–10 тезисов. Повторяющиеся детали можно объединить, содержание тем не отбрасывай.

Верни только Markdown. Структура: заголовок, краткое описание охвата, затем тематические разделы с конкретными тезисами.

Название: {title or 'неизвестно'}
Источник: {source or 'локальный файл'}

ЗАМЕТКИ ПО ВСЕМ ЧАСТЯМ:
{notes}
"""


def _sentences(text):
    return [x.strip() for x in re.split(r'(?<=[.!?…])\s+', re.sub(r'\s+', ' ', str(text or '').strip())) if x.strip()]


def _fallbacks(title, text):
    """Полный extractive fallback по всем частям вместо шаблонных пустых тезисов."""
    chunks = _split_text(text, limit=CHUNK_CHARS, overlap=0)
    # Sample the whole recording for the short fallback, not only its opening.
    sampled = []
    if chunks:
        count = min(8, len(chunks))
        indices = sorted({round(i * (len(chunks) - 1) / max(1, count - 1)) for i in range(count)})
        for i in indices:
            choices = [s for s in _sentences(chunks[i]) if len(s) > 55]
            if choices:
                sampled.append(choices[0])
    short = ' '.join(sampled).strip()
    if len(short) > 1300:
        short = short[:1297].rsplit(' ', 1)[0] + '…'
    brief = f"# Краткое резюме\n\n{short or 'Содержание не удалось извлечь из расшифровки.'}\n"
    topics = [f"# Подробный тематический конспект\n\nЛокальная выборка по всей расшифровке; автоматическая тематическая группировка недоступна, поэтому пункты требуют редакторской проверки. Фрагментов: {len(chunks)}.\n"]
    for i, chunk in enumerate(chunks, 1):
        sentences = [s for s in _sentences(chunk) if len(s) > 45]
        chosen = sentences[:6]
        if chosen:
            topics.append(f"\n## Участок расшифровки {i}\n\n" + '\n'.join(f'- {s}' for s in chosen))
    return brief.rstrip() + '\n', '\n'.join(topics).rstrip() + '\n'


def _brief_from_verified_notes(notes, per_part=3):
    """Keep a compact, evidence-backed overview if cross-part synthesis fails."""
    out = ['# Краткое резюме по проверенным тезисам']
    count = 0
    active_part = False
    for line in str(notes or '').splitlines():
        if line.startswith('### Фрагмент '):
            count = 0
            active_part = True
            out.append('\n## ' + line.removeprefix('### ').strip())
        elif re.match(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)', line) and count < per_part:
            out.append(line)
            count += 1
    return '\n'.join(out).strip() + '\n'


def _normalize(value):
    value = str(value or '').replace('\x00', '').strip()
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value


def _anchor_chunk_note(note, source):
    """Verify exact quoted phrases in source and add their source times automatically."""
    rows = []
    for line in source.splitlines():
        match = re.match(r'^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$', line)
        if match:
            h, m, s = (int(x) for x in match.group(1).split(':'))
            rows.append((h * 3600 + m * 60 + s, match.group(2)))
    if not rows:
        return None
    normalize = lambda value: re.findall(r'[\w]+', str(value).lower().replace('ё', 'е'))
    tokens, original, times = [], [], []
    for at, text in rows:
        row_matches = list(re.finditer(r'[\w]+', text))
        tokens.extend([m.group(0).lower().replace('ё', 'е') for m in row_matches])
        original.extend([m.group(0) for m in row_matches])
        times.extend([at] * len(row_matches))
    blocks, preamble, active = [], [], None
    for line in note.splitlines():
        if re.match(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)', line):
            if active is not None:
                blocks.append(active)
            active = [line]
        elif active is not None and line.strip() and not line.lstrip().startswith('#'):
            active.append(line)
        else:
            if active is not None:
                blocks.append(active); active = None
            preamble.append(line)
    if active is not None:
        blocks.append(active)
    if not blocks:
        return None
    rebuilt = []
    cursor = 0
    rejected = 0
    for block in blocks:
        body = '\n'.join(block)
        quoted = re.search(r'«([^»]{8,})»', body)
        if not quoted:
            rejected += 1
            continue
        quote_words = normalize(quoted.group(1))
        match = SequenceMatcher(None, tokens[cursor:], quote_words, autojunk=False).find_longest_match()
        if match.size < 6 or match.size / max(1, len(quote_words)) < .35:
            rejected += 1
            continue
        first = cursor + match.a
        last = first + match.size - 1
        if times[last] - times[first] > 120:
            rejected += 1
            continue
        exact_quote = ' '.join(original[first:last + 1])
        claim_text = body.replace(quoted.group(0), '')
        for number in re.findall(r'(?<!\w)(\d{1,3})\s*%', claim_text):
            spelled = {'10': r'десят\w*', '20': r'двадцат\w*', '30': r'тридцат\w*',
                       '40': r'сорок\w*', '50': r'пятьдесят\w*', '60': r'шестьдесят\w*',
                       '70': r'семьдесят\w*', '80': r'восемьдесят\w*', '90': r'девяност\w*'}.get(number)
            if number not in quote_words and not (spelled and re.search(spelled, ' '.join(quote_words))):
                rejected += 1
                break
        else:
            begin, finish = times[first], times[last]
            stamp = f'[{_timecode(begin)}]' if begin == finish else f'[{_timecode(begin)}–{_timecode(finish)}]'
            line = block[0]
            marker = re.match(r'^(\s*(?:[-*+]\s+|\d+[.)]\s+))', line)
            if not marker:
                rejected += 1
                continue
            line = line[:marker.end()] + stamp + ' ' + line[marker.end():]
            body = body.replace(quoted.group(0), f'«{exact_quote}»')
            block = body.splitlines()
            block[0] = line
            rebuilt.extend(block)
            cursor = last + 1
    if len(rebuilt) < 3:
        return None
    return '\n'.join(preamble + rebuilt).strip()


def _extractive_chunk_note(source, index, total):
    """Auditable local reserve for a chunk whose generated quotes could not be verified."""
    rows = []
    for line in source.splitlines():
        match = re.match(r'^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$', line)
        if match and match.group(2).strip():
            rows.append((match.group(1), match.group(2).strip()))
    if not rows:
        return f'## Фрагмент {index}/{total}\n\nВ источнике нет распознанных строк с таймкодами.'
    count = min(8, len(rows))
    indices = sorted({round(i * (len(rows) - 1) / max(1, count - 1)) for i in range(count)})
    out = [f'## Фрагмент {index}/{total}: цитатная выборка (модельная группировка не подтверждена)']
    for row_index in indices:
        stamp, text = rows[row_index]
        words = re.findall(r'[\w]+', text)
        quote = ' '.join(words[:min(10, len(words))])
        if len(text) > 500:
            text = text[:497].rsplit(' ', 1)[0] + '…'
        quote = quote.replace('«', '').replace('»', '')
        out.append(f'- [{stamp}] {text} Опора: «{quote}»')
    return '\n'.join(out)


def _timecode(value):
    n = max(0, int(value))
    return f'{n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}'


def _valid_final_topics(value, notes, require_coverage=False):
    """Final synthesis may reorganize notes, but must retain their exact evidence."""
    normalize = lambda text: ' '.join(re.findall(r'[\w]+', str(text).lower().replace('ё', 'е')))
    evidence = {}
    blocks = []
    for line in notes.splitlines():
        if re.match(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)', line):
            blocks.append([line])
        elif blocks and line.strip() and not line.lstrip().startswith('#'):
            blocks[-1].append(line)
    for block in blocks:
        body = '\n'.join(block)
        quote = re.search(r'«([^»]{8,})»', body)
        cites = re.findall(r'\[(\d{2}:\d{2}:\d{2}(?:\s*[–—-]\s*\d{2}:\d{2}:\d{2})?)\]', body)
        if quote:
            for cite in cites:
                evidence[cite] = normalize(quote.group(1))
    claims = []
    for line in value.splitlines():
        if re.match(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)', line):
            claims.append([line])
        elif claims and line.strip() and not line.lstrip().startswith('#'):
            claims[-1].append(line)
    if not claims:
        return False
    for block in claims:
        body = '\n'.join(block)
        quote = re.search(r'«([^»]{8,})»', body)
        cites = re.findall(r'\[(\d{2}:\d{2}:\d{2}(?:\s*[–—-]\s*\d{2}:\d{2}:\d{2})?)\]', body)
        if not quote or not cites or not any(c in evidence and normalize(quote.group(1)) in evidence[c] for c in cites):
            return False
        for number in re.findall(r'(?<!\w)(\d{1,3})\s*%', body.replace(quote.group(0), '')):
            if number not in normalize(quote.group(1)):
                return False
    if require_coverage:
        parts = re.split(r'(?m)^### Фрагмент \d+/\d+[^\n]*\n', notes)[1:]
        final_times = {stamp.split('–', 1)[0].split('—', 1)[0].split('-', 1)[0].strip()
                       for line in value.splitlines()
                       for stamp in re.findall(r'\[(\d{2}:\d{2}:\d{2}(?:\s*[–—-]\s*\d{2}:\d{2}:\d{2})?)\]', line)}
        for part in parts:
            part_times = set(re.findall(r'^\s*[-*+]\s+\[(\d{2}:\d{2}:\d{2})', part, re.M))
            if part_times and not part_times.intersection(final_times):
                return False
    return True


def ensure_summary(directory, title='', source='', generate=None, notify=lambda message: None, force=False):
    """Создаёт краткое резюме, подробный тематический конспект и JSON-аудит."""
    directory = Path(directory)
    clean = directory / 'transcript.clean.md'
    brief_path = directory / SUMMARY_FILE
    topics_path = directory / TOPICS_FILE
    meta_path = directory / META_FILE
    if not clean.is_file():
        raise FileNotFoundError('Нет чистовой расшифровки для общего резюме')
    clean_text = clean.read_text(encoding='utf-8')
    timed = directory / 'transcript-timed.txt'
    text = timed.read_text(encoding='utf-8') if timed.is_file() else clean_text
    fingerprint = hashlib.sha256((clean_text + '\0' + text).encode('utf-8')).hexdigest()
    if not force and brief_path.is_file() and topics_path.is_file() and meta_path.is_file():
        try:
            old = json.loads(meta_path.read_text(encoding='utf-8'))
            if old.get('version') == VERSION and old.get('source_sha256') == fingerprint:
                return dict(old, path=SUMMARY_FILE, topics_path=TOPICS_FILE, cached=True)
        except (OSError, ValueError):
            pass
    chunks = _split_text(text)
    notify(f'Извлекаю темы из полной расшифровки: {len(chunks)} частей…')
    method, generation_error = 'local-extractive-fallback', ''
    notes = []
    all_notes = ''
    fallback_chunks = 0
    if generate and chunks:
        try:
            for i, chunk in enumerate(chunks, 1):
                notify(f'Анализирую фрагмент {i}/{len(chunks)} расшифровки…')
                try:
                    note = _normalize(generate(_chunk_prompt(title, source, i, len(chunks), chunk)))
                    anchored = _anchor_chunk_note(note, chunk) if note else None
                    if anchored is None:
                        retry = ("Перепиши анализ этого фрагмента. Каждый пункт должен содержать короткую непрерывную фразу в кавычках «…», "
                                 "дословно скопированную из одного места источника. Не придумывай цитаты и не добавляй многоточия; "
                                 "удали утверждения, которые нельзя подтвердить. Таймкоды не пиши — их добавит программа.\n\n"
                                 + _chunk_prompt(title, source, i, len(chunks), chunk) + "\n\nПЕРВАЯ ВЕРСИЯ ДЛЯ ПРОВЕРКИ:\n" + (note or ''))
                        note = _normalize(generate(retry))
                        anchored = _anchor_chunk_note(note, chunk) if note else None
                    if anchored is None:
                        fallback_chunks += 1
                        notify(f'Фрагмент {i}: сохраняю дословную выборку с таймкодами после неудачной проверки цитат.')
                        note = _extractive_chunk_note(chunk, i, len(chunks))
                    else:
                        note = anchored
                except Exception as part_error:
                    if 'остановили' in str(part_error).lower():
                        raise
                    fallback_chunks += 1
                    notify(f'Фрагмент {i}: модель недоступна, сохраняю дословную выборку с таймкодами.')
                    note = _extractive_chunk_note(chunk, i, len(chunks))
                notes.append(f'### Фрагмент {i}/{len(chunks)}\n{note}')
            all_notes = '\n\n'.join(notes)
            notify('Собираю краткое резюме по всей записи…')
            brief_error = ''
            try:
                brief = _normalize(generate(_final_prompt(title, source, all_notes, 'short')))
                if not brief or not _valid_final_topics(brief, all_notes, require_coverage=True):
                    retry = (_final_prompt(title, source, all_notes, 'short') +
                             "\n\nПРОВЕРКА: у каждого пункта должны быть точная цитата и таймкод из заметок. "
                             "Скопируй их без изменений; удали пункт, если опоры нет.\n\nПЕРВАЯ ВЕРСИЯ:\n" + brief)
                    brief = _normalize(generate(retry))
                if not brief or not _valid_final_topics(brief, all_notes, require_coverage=True):
                    raise ValueError('краткое резюме потеряло проверяемые цитаты или таймкоды')
            except Exception as exc:
                if 'остановили' in str(exc).lower():
                    raise
                brief_error = str(exc)[:300]
                notify('Краткое резюме не прошло проверку — формирую его по проверенным тезисам с таймкодами.')
                brief = _brief_from_verified_notes(all_notes)
            notify('Собираю подробный конспект тем по всей записи…')
            try:
                topics = _normalize(generate(_final_prompt(title, source, all_notes, 'topics')))
                if not topics:
                    raise ValueError('модель не вернула подробный конспект')
                if not _valid_final_topics(topics, all_notes, require_coverage=True):
                    retry = (_final_prompt(title, source, all_notes, 'topics') +
                             "\n\nПЕРЕПИШИ: в каждом пункте должен быть точный таймкод и дословная цитата из заметок выше. "
                             "Копируй цитату и таймкод без изменения. Удали любые утверждения без такой опоры.\n\nПЕРВАЯ ВЕРСИЯ:\n" + topics)
                    topics = _normalize(generate(retry))
                if not topics or not _valid_final_topics(topics, all_notes, require_coverage=True):
                    raise ValueError('итоговый конспект потерял точные цитаты или таймкоды')
            except Exception as exc:
                if 'остановили' in str(exc).lower():
                    raise
                generation_error = str(exc)[:500]
                notify('Итоговая перегруппировка не прошла проверку — оставляю подробные проверенные заметки по всем участкам.')
                topics = '# Подробный тематический конспект по частям\n\n' + all_notes
            if not brief.startswith('#'):
                brief = '# Краткое резюме\n\n' + brief
            if not topics.startswith('#'):
                topics = '# Подробный тематический конспект\n\n' + topics
            method = ('hierarchical-llm-with-evidence-notes' if generation_error or brief_error else
                      'hierarchical-llm-with-extractive-parts' if fallback_chunks else 'hierarchical-llm')
            if brief_error:
                generation_error = (generation_error + '; ' if generation_error else '') + 'краткое резюме: ' + brief_error
        except Exception as exc:  # summary must not destroy completed transcription
            if 'остановили' in str(exc).lower():
                raise
            generation_error = str(exc)[:500]
            if notes and len(notes) == len(chunks):
                notify('Финальная сборка не прошла проверку — сохраняю подробные проверенные заметки по всем участкам.')
                brief, _ = _fallbacks(title, text)
                topics = '# Подробный тематический конспект по частям\n\n' + all_notes
                method = 'chunked-llm-evidence'
            else:
                notify('Модель анализа недоступна — сохраняю выборку по всем участкам расшифровки.')
                brief, topics = _fallbacks(title, text)
    else:
        brief, topics = _fallbacks(title, text)
    for path, value in ((brief_path, brief), (topics_path, topics)):
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(value.rstrip() + '\n', encoding='utf-8')
        tmp.replace(path)
    meta = dict(version=VERSION, source_sha256=fingerprint, method=method,
                title=title or '', source=source or '', source_characters=len(text), clean_characters=len(clean_text),
                chunks=len(chunks), extractive_chunks=fallback_chunks,
                summary_characters=len(brief), topics_characters=len(topics),
                path=SUMMARY_FILE, topics_path=TOPICS_FILE,
                model=getattr(generate, 'model_info', None) if generate else None)
    if generation_error:
        meta['generation_error'] = generation_error
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return meta
