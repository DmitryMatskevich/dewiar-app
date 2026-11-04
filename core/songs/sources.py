"""Source discovery and audio acquisition, with explicit recording identity."""
import json
import hashlib
import re
import threading
import uuid
from difflib import SequenceMatcher
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _tokens(value):
    return re.findall(r"[a-zа-яё0-9]+", str(value or "").casefold().replace('ё', 'е'))


def _rank(candidate, title, artist):
    wanted_title, wanted_artist = _tokens(title), _tokens(artist)
    got_title, got_artist = _tokens(candidate.get('title')), _tokens(candidate.get('artist'))
    def overlap(a, b):
        return len(set(a) & set(b)) / max(1, len(set(a)))
    inferred_artist = [token for token in wanted_title if token in got_artist]
    clean_title = [token for token in wanted_title if token not in inferred_artist]
    title_tokens = clean_title or wanted_title
    title_score = max(overlap(title_tokens, got_title), SequenceMatcher(None, ' '.join(title_tokens), ' '.join(got_title)).ratio())
    artist_target = wanted_artist or inferred_artist
    artist_pool = got_artist + (got_title if wanted_artist else [])
    artist_score = overlap(artist_target, artist_pool) if artist_target else 0.5
    candidate['match_score'] = round(.78 * title_score + .22 * artist_score, 3)
    candidate['artist_match'] = round(artist_score, 3)
    return candidate


def discover(query, references=None, title=None, artist=''):
    import yt_dlp
    candidates, errors = [], []
    ranking_artist = artist
    if not ranking_artist and re.search(r'[а-яё]', query, re.I):
        trailing_latin = re.search(r'([a-z][a-z0-9._-]*)\s*$', query, re.I)
        if trailing_latin:
            ranking_artist = trailing_latin.group(1)
    # Search title/artist both together and separately: YouTube's flat search can
    # bury an exact match when the query contains transliteration or extra words.
    queries = list(dict.fromkeys([query, re.sub(r"\s+", " ", query.replace('ZZarubin', 'Z Zarubin')), query.split(' — ')[0]]))
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True,
                              'extract_flat': True, 'socket_timeout': 15, 'retries': 1}) as dl:
            entries = []
            for search_query in queries:
                result = dl.extract_info('ytsearch10:'+search_query, download=False)
                entries.extend((result or {}).get('entries') or [])
        for item in entries:
            video_id = item.get('id', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                continue
            candidates.append(_rank({'candidate_id': 'youtube:'+video_id, 'kind': 'youtube',
                'title': item.get('title', ''), 'artist': item.get('channel') or item.get('uploader') or '',
                'duration': item.get('duration'), 'url': 'https://www.youtube.com/watch?v='+video_id,
                'version_verified': False}, title or query, ranking_artist))
    except Exception as exc:
        errors.append({'kind': 'youtube', 'error': str(exc)[:300]})
    try:
        request = Request('https://lrclib.net/api/search?'+urlencode({'q': query}),
                          headers={'User-Agent': 'Dewiar-Songs/0.1 (personal music study)'})
        with urlopen(request, timeout=15) as response:
            rows = json.loads(response.read(2_000_001))
        for row in rows[:10]:
            candidates.append({'candidate_id': 'lrclib:'+str(row['id']), 'kind': 'lyrics',
                'title': row.get('trackName'), 'artist': row.get('artistName'),
                'duration': row.get('duration'), 'url': 'https://lrclib.net/api/get/'+str(row['id']),
                'plain_lyrics': str(row.get('plainLyrics') or '')[:100_000],
                'synced_lyrics': str(row.get('syncedLyrics') or '')[:100_000],
                'version_verified': False})
    except Exception as exc:
        errors.append({'kind': 'lrclib', 'error': str(exc)[:300]})
    # Preserve supplied URLs and search the chord source explicitly. AMDM pages
    # are references only; they are never treated as a verified recording.
    for url in references or []:
        candidates.append({'candidate_id': 'reference:'+hashlib.sha256(url.encode('utf-8')).hexdigest()[:16], 'kind': 'reference',
            'title': 'Источник, указанный пользователем', 'artist': '', 'url': url,
            'note': 'Ссылка пользователя — проверьте соответствие версии песни.', 'version_verified': False})
    from urllib.parse import quote
    slug = re.sub(r'[^a-z0-9а-яё]+', '_', query.casefold()).strip('_')
    if slug:
        candidates.append({'candidate_id': 'yandex-search:'+slug, 'kind': 'reference',
            'title': query+' — поиск в Яндекс Музыке', 'artist': '',
            'url': 'yandexmusic://search?text='+quote(query),
            'note': 'Откройте результаты в приложении, выберите нужное исполнение, затем повторно считайте текущий трек.',
            'version_verified': False})
        for suffix, label, note in [
            ('аккорды site:amdm.ru/akkordi', 'Аккорды AMDM', 'Найденные страницы аккордов AMDM требуют ручной проверки версии.'),
            ('текст песни', 'Текст песни', 'Поиск опубликованного текста для сверки с распознаванием.'),
        ]:
            candidates.append({'candidate_id': 'web-search:'+slug+':'+label, 'kind': 'reference',
                'title': query+' — '+label, 'artist': '',
                'url': 'https://www.google.com/search?q='+quote(query+' '+suffix),
                'note': note, 'version_verified': False})
    unique = {}
    for item in candidates:
        unique.setdefault(item['candidate_id'], item)
    candidates = [c for c in unique.values() if c.get('kind') != 'youtube' or (
        c.get('match_score', 0) >= .4 and (not ranking_artist or c.get('artist_match', 0) >= .25))]
    candidates = sorted(candidates, key=lambda c: (c.get('match_score', 0), c.get('kind') == 'reference'), reverse=True)[:30]
    return candidates, errors


class SongSources:
    def __init__(self, store, emit):
        self.store, self.emit = store, emit
        self._lock, self._jobs = threading.RLock(), {}

    def running(self, song_id):
        with self._lock:
            return song_id in self._jobs

    def cancel(self, song_id):
        with self._lock:
            if song_id in self._jobs:
                self._jobs[song_id].set()

    def start(self, song_id, candidate_id=None):
        with self._lock, self.store._lock:
            row = self.store.get(song_id)
            if not row:
                raise ValueError('Песня не найдена')
            if self.running(song_id) or row.get('status') in {'running', 'recording'}:
                raise ValueError('Сначала завершите текущий этап')
            candidate = None
            if candidate_id:
                if row.get('audio', {}).get('path') or row.get('phrases'):
                    raise ValueError('Для другой записи создайте отдельную карточку')
                candidate = next((c for c in row.get('source_candidates', [])
                                  if c.get('candidate_id') == candidate_id and c.get('kind') == 'youtube'), None)
                if not candidate:
                    raise ValueError('Выберите найденную запись YouTube')
            run = str(uuid.uuid4())
            cancel = threading.Event()
            self._jobs[song_id] = cancel
            updated = self.store.patch(song_id, {'status': 'running', 'stage': 'sources',
                'active_run_id': run, 'stage_label': 'Сохраняем выбранную запись' if candidate else 'Ищем записи и опубликованный текст'})
            threading.Thread(target=self._run, args=(row, run, cancel, candidate),
                             name='song-sources-'+song_id[:8], daemon=True).start()
            return updated

    def _run(self, row, run, cancel, candidate):
        song_id = row['song_id']
        try:
            if candidate:
                from media_transcription import _audio
                folder = self.store.root/song_id/'audio'/run
                folder.mkdir(parents=True, exist_ok=True)
                path, info = _audio(candidate['url'], folder, cancel)
                changes = {'audio': {'path': str(path.relative_to(self.store.root)),
                    'format': path.suffix[1:], 'duration_ms': round(float(info.get('duration') or 0)*1000),
                    'owned': True, 'original_name': info.get('title') or path.name},
                    'source': 'url', 'source_ref': candidate['url'],
                    'recording_version': info.get('title') or candidate['title'],
                    'sources': [*row.get('sources', []), {**candidate, 'selected': True}],
                    'status': 'queued', 'stage': 'lyrics', 'stage_label': 'Запись сохранена — можно начать полный разбор',
                    'capabilities': {**row.get('capabilities', {}), 'audio': True}}
            else:
                search_query = ' '.join([row['title'], row.get('artist', '')]).strip()
                candidates, errors = discover(search_query,
                    [s.get('url') for s in row.get('sources', []) if s.get('kind') == 'reference' and s.get('url')],
                    title=row['title'], artist=row.get('artist', ''))
                has_audio = bool(row.get('audio',{}).get('path'))
                retained = [c for c in row.get('source_candidates', []) if c.get('kind') == 'reference' or str(c.get('candidate_id','')).startswith('reference:')]
                ids = {c.get('candidate_id') for c in retained}
                changes = {'source_candidates': [*retained, *[c for c in candidates if c.get('candidate_id') not in ids]],
                    'status': row.get('status','review') if has_audio else 'needs_input',
                    'stage': row.get('stage','review') if has_audio else 'sources',
                    'stage_label': ('Источники для сверки найдены' if has_audio else 'Выберите исполнение: версии песни могут отличаться') if candidates else 'Источники не найдены; сохранённая запись доступна' if has_audio else 'Источники не найдены; можно импортировать запись',
                    'sources': [*row.get('sources', []), *[{'kind': 'source_error', 'provider':e['kind'], 'error':e['error']} for e in errors]]}
            with self.store._lock:
                fresh = self.store.get(song_id) or {}
                if cancel.is_set() or fresh.get('active_run_id') != run:
                    return
                updated = self.store.patch(song_id, {**changes, 'active_run_id': None})
            self.emit('song_analysis', song_id=song_id, running=False, song=updated)
        except Exception as exc:
            with self.store._lock:
                fresh = self.store.get(song_id) or {}
                if cancel.is_set() or fresh.get('active_run_id') != run:
                    return
                updated = self.store.patch(song_id, {'status': 'failed', 'active_run_id': None,
                    'stage_label': 'Ошибка источника: '+str(exc)[:180]})
            self.emit('song_analysis', song_id=song_id, running=False, song=updated)
        finally:
            with self._lock:
                self._jobs.pop(song_id, None)
