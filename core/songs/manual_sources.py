"""User-supplied references: preserve the recording and all reviewed edits."""
import hashlib
import re
from urllib.parse import parse_qs, urlsplit


def youtube_video_id(url):
    try:
        parsed = urlsplit(str(url or '').strip())
        host = (parsed.hostname or '').lower().rstrip('.')
        video_id = ''
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
            video_id = (parse_qs(parsed.query).get('v') or [''])[0] if parsed.path == '/watch' else ''
            if parsed.path.startswith(('/shorts/', '/live/', '/embed/')):
                video_id = parsed.path.split('/')[2]
        elif host in ('youtu.be', 'www.youtu.be'):
            video_id = parsed.path.strip('/').split('/')[0]
        return video_id if re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) else ''
    except (ValueError, IndexError):
        return ''


def add_manual_source(store, song_id, url='', lyrics='', base_revision=None):
    url = str(url or '').strip()
    lyrics = str(lyrics or '').strip()
    if not url and not lyrics:
        raise ValueError('Укажите ссылку или вставьте текст для сверки')
    if len(url) > 2048 or len(lyrics) > 100000:
        raise ValueError('Ссылка или текст слишком длинные')
    video_id = ''
    if url:
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower().rstrip('.')
            if parsed.scheme not in ('http', 'https') or not host or parsed.username or parsed.password or any(c.isspace() for c in url):
                raise ValueError()
            video_id = youtube_video_id(url)
        except (ValueError, IndexError):
            raise ValueError('Введите корректную ссылку http или https') from None
    if base_revision is None:
        raise ValueError('Обновите карточку перед добавлением источника')
    with store._lock:
        row = store.get(song_id)
        if not row:
            raise ValueError('Песня не найдена')
        if row.get('status') in {'running', 'recording'}:
            raise ValueError('Сначала завершите текущую обработку или запись')
        identity = hashlib.sha256((url + '\n' + lyrics).encode('utf-8')).hexdigest()[:20]
        candidates = list(row.get('source_candidates') or [])
        additions = []
        if video_id:
            additions.append({'candidate_id': 'reference:' + identity + ':audio', 'kind': 'youtube',
                'title': 'Запись по ссылке пользователя', 'artist': '',
                'url': 'https://www.youtube.com/watch?v=' + video_id,
                'note': 'Сверьте исполнителя и версию перед выбором записи.', 'version_verified': False})
        elif url:
            additions.append({'candidate_id': 'reference:' + identity + ':link', 'kind': 'reference',
                'title': 'Ссылка пользователя', 'artist': '', 'url': url,
                'note': 'Ссылка для ручной проверки; страница не загружается автоматически.', 'version_verified': False})
        if lyrics:
            additions.append({'candidate_id': 'reference:' + identity + ':lyrics', 'kind': 'lyrics',
                'title': 'Текст пользователя для сверки', 'artist': '', 'url': url,
                'plain_lyrics': lyrics, 'note': 'Сверьте версию: текст не заменяет распознавание.', 'version_verified': False})
        known = {item.get('candidate_id') for item in candidates}
        candidates.extend(item for item in additions if item['candidate_id'] not in known)
        if len(candidates) > 200:
            raise ValueError('Слишком много источников в карточке')
        return store.patch(song_id, {'source_candidates': candidates}, base_revision)
