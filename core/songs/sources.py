"""Source discovery and audio acquisition, with explicit recording identity."""
import json
import re
import threading
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def discover(query):
    import yt_dlp
    candidates, errors = [], []
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True,
                              'extract_flat': True, 'socket_timeout': 15, 'retries': 1}) as dl:
            result = dl.extract_info('ytsearch5:'+query, download=False)
        for item in (result or {}).get('entries') or []:
            video_id = item.get('id', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                continue
            candidates.append({'candidate_id': 'youtube:'+video_id, 'kind': 'youtube',
                'title': item.get('title', ''), 'artist': item.get('channel') or item.get('uploader') or '',
                'duration': item.get('duration'), 'url': 'https://www.youtube.com/watch?v='+video_id,
                'version_verified': False})
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
                candidates, errors = discover(' '.join([row['title'], row.get('artist', '')]).strip())
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
