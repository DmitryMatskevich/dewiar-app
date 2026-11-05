"""Resume saved song jobs without depending on an open Studio page."""
import threading


class SongProcessing:
    def __init__(self, store, analyzer, sources, capture):
        self.store, self.analyzer, self.sources, self.capture = store, analyzer, sources, capture
        self._lock = threading.RLock()

    def describe(self, row):
        sid = row['song_id']
        busy = (self.analyzer.running(sid) or self.sources.running(sid)
                or self.capture.state(sid).get('running', False))
        audio = row.get('audio') or {}
        path = (self.store.root / str(audio.get('path') or '')).resolve()
        has_audio = self.store.root.resolve() in path.parents and path.is_file()
        selected = next((c for c in row.get('source_candidates', [])
                         if c.get('candidate_id') == row.get('selected_source_id')
                         and c.get('kind') == 'youtube'), None)
        incomplete = (row.get('status') in {'queued', 'interrupted', 'failed', 'cancelled', 'partial', 'running', 'recording', 'needs_input'}
                      or row.get('analysis_state') == 'full_partial'
                      or not row.get('capabilities', {}).get('lyrics')
                      or not row.get('capabilities', {}).get('chords'))
        can_resume = not busy and incomplete and (has_audio or bool(selected) and not audio.get('path') and not row.get('phrases'))
        return {'busy': bool(busy), 'can_resume': bool(can_resume),
                'label': 'Продолжить обработку' if has_audio or row.get('status') in {'failed', 'interrupted', 'cancelled'} else 'Обработать пакетно'}

    def resume(self, song_id):
        with self._lock:
            row = self.store.get(song_id)
            if not row:
                raise ValueError('Песня не найдена')
            info = self.describe(row)
            if info['busy']:
                return row  # repeated clicks cannot start another worker
            if not info['can_resume']:
                raise ValueError('Добавьте аудиофайл или выберите запись для обработки; готовая песня доступна для проверки')
            if row.get('audio', {}).get('path'):
                # Reconnect to a detached worker before considering a new run.
                if row.get('status') in {'running', 'recording'}:
                    row = self.store.patch(song_id, {'status': 'interrupted',
                        'interrupted_run_id': row.get('active_run_id'), 'active_run_id': None})
                if row.get('status') == 'interrupted':
                    self.analyzer.resume_interrupted(song_id)
                    return self.store.get(song_id)
                return self.analyzer.start(song_id, full=True)
            if row.get('status') in {'running', 'recording'}:
                self.store.patch(song_id, {'status': 'interrupted', 'active_run_id': None})
            return self.sources.start(song_id, row['selected_source_id'], process=True)
