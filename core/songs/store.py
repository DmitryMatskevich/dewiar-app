"""Atomic local storage for the Songs studio tab.

Song records are user data.  Writes use a temporary file and replace so a
crash cannot leave a half-written song card.  Audio ownership is represented
explicitly; deleting a card never deletes a file the user supplied.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path


STAGES = ("identify", "sources", "record", "lyrics", "structure", "chords", "review")
STATUS = {"queued", "recording", "running", "needs_input", "review", "ready", "partial", "failed", "cancelled", "interrupted"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _clean(value: object, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


class SongStore:
    schema_version = 1

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()

    def _read_index(self) -> list[str]:
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
            ids = [str(x) for x in value if isinstance(x, str)] if isinstance(value, list) else []
        except (FileNotFoundError, OSError, ValueError):
            ids = []
        # Карточка — источник истины: сбой между её записью и записью индекса
        # не должен скрыть песню после перезапуска.
        found = sorted(p.parent.name for p in self.root.glob('*/song.json'))
        return list(dict.fromkeys([*ids, *found]))

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass

    def _path(self, song_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9-]{20,40}", song_id):
            raise ValueError("Некорректный идентификатор песни")
        return self.root / song_id / "song.json"

    def _read(self, song_id: str) -> dict | None:
        try:
            data = json.loads(self._path(song_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def list(self, query: str = "", status: str = "", limit: int = 100) -> list[dict]:
        query = _clean(query, 120).casefold()
        with self._lock:
            rows = []
            for song_id in reversed(self._read_index()):
                row = self._read(song_id)
                if not row:
                    continue
                haystack = f"{row.get('title', '')} {row.get('artist', '')}".casefold()
                if query and query not in haystack:
                    continue
                selected_statuses = {"queued", "recording", "running"} if status == "running" else {status}
                if status and row.get("status") not in selected_statuses:
                    continue
                rows.append(copy.deepcopy(row))
                if len(rows) >= max(1, min(int(limit or 100), 500)):
                    break
            return rows

    def get(self, song_id: str) -> dict | None:
        with self._lock:
            value = self._read(song_id)
            return copy.deepcopy(value) if value else None

    def create(self, title: str, artist: str = "", source: str = "query", source_ref: str = "") -> dict:
        title, artist = _clean(title), _clean(artist)
        if not title:
            raise ValueError("Укажите название песни или поисковый запрос")
        song_id = str(uuid.uuid4())
        now = _now()
        row = {
            "song_id": song_id,
            "schema_version": self.schema_version,
            "revision": 1,
            "title": title,
            "artist": artist,
            "recording_version": "",
            "source": source if source in {"current", "file", "url", "query", "batch"} else "query",
            "source_ref": _clean(source_ref, 2000),
            "status": "queued",
            "analysis_state": "queued",
            "review_state": "unreviewed",
            "stage": "identify",
            "stage_label": "Готово к определению песни",
            "coverage": {"known_ms": 0, "recorded_ms": 0, "gaps": []},
            "audio": {"path": "", "format": "wav", "sample_rate": 16000, "channels": 1, "owned": True, "duration_ms": 0},
            "capabilities": {"lyrics": False, "structure": False, "chords": False, "audio": False},
            "sections": [],
            "chords": [],
            "guitar_sheet": [],
            "phrases": [],
            "words": [],
            "sources": [],
            "edits": [],
            "analysis_runs": [],
            "phrase_history": {},
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._write_json(self.root / song_id / "song.json", row)
            ids = self._read_index()
            ids = [x for x in ids if x != song_id]
            ids.append(song_id)
            self._write_json(self.index_path, ids)
        return copy.deepcopy(row)

    def patch(self, song_id: str, changes: dict, base_revision: int | None = None) -> dict:
        if not isinstance(changes, dict):
            raise ValueError("Правки песни должны быть объектом")
        with self._lock:
            row = self._read(song_id)
            if not row:
                raise ValueError("Песня не найдена")
            current_revision = int(row.get("revision") or 1)
            if base_revision is not None and int(base_revision) != current_revision:
                raise ValueError("Песня уже изменилась — обновите карточку и повторите правку")
            allowed = {"title", "artist", "source", "source_ref", "recording_version", "status", "analysis_state", "review_state",
                       "stage", "stage_label", "coverage", "audio", "capabilities", "sections", "phrases", "words", "sources", "chords", "guitar_sheet",
                       "analysis_runs", "phrase_history", "active_run_id", "interrupted_run_id", "selected_run_id", "candidate_run_id", "timing_quality",
                       "harmony", "analysis_errors", "source_candidates"}
            if "title" in changes and not _clean(changes["title"]):
                raise ValueError("Укажите название песни")
            if "status" in changes and changes["status"] not in STATUS:
                raise ValueError("Неизвестное состояние песни")
            for key, value in changes.items():
                if key in allowed:
                    row[key] = _clean(value) if key in {"title", "artist"} else copy.deepcopy(value)
            row.setdefault("edits", []).append({"at": _now(), "changes": copy.deepcopy(changes)})
            row["revision"] = current_revision + 1
            row["updated_at"] = _now()
            self._write_json(self._path(song_id), row)
            return copy.deepcopy(row)

    def recover_interrupted(self):
        """Вызывать при запуске владельца задач, когда старых workers уже нет."""
        for row in self.list(limit=500):
            if row.get("status") in {"recording", "running"}:
                self.patch(row["song_id"], {"status": "interrupted", "interrupted_run_id": row.get("active_run_id"), "active_run_id": None,
                    "stage_label": "Обработка прервана — сохранённые результаты доступны"})

    def save_analysis(self, song_id, run_id, phrases, model):
        """Сохраняет сырую гипотезу; новый запуск не заменяет проверенный текст."""
        from .structure import infer_sections
        with self._lock:
            row = self.get(song_id)
            if not row or row.get("active_run_id") != run_id or row.get("status") != "running":
                return None
            run = {"run_id": run_id, "created_at": _now(), "model": model,
                   "audio_path": row.get("audio", {}).get("path"), "phrases": copy.deepcopy(phrases),
                   "timing_quality": "phrase", "words": None}
            # Несопоставленные границы нового ASR нельзя применить к прежним
            # пользовательским правкам. Оставляем вариант для отдельного выбора.
            keep = bool(row.get("phrase_history"))
            selected = row.get("phrases", []) if keep else phrases
            changes = {"analysis_runs": [*row.get("analysis_runs", []), run], "active_run_id": None,
                       "candidate_run_id": run_id if keep else None,
                       "status": "review" if selected else "partial", "analysis_state": "lyrics_ready" if phrases else "lyrics_empty",
                       "stage": "review", "review_state": "new_candidate" if keep else "needs_review",
                       "stage_label": "Новый вариант сохранён; ваши правки оставлены" if keep else "Проверьте текст по записи",
                       "capabilities": {**row.get("capabilities", {}), "lyrics": bool(selected), "structure": bool(selected)}}
            if not keep:
                changes.update(phrases=selected, words=[], timing_quality="phrase", selected_run_id=run_id,
                               sections=infer_sections(selected), guitar_sheet=[])
            return self.patch(song_id, changes)

    def save_full_analysis(self, song_id, run_id, basic, model, result):
        from .consensus import consensus, choose_spine, recover_omissions
        from .chord_layout import build_guitar_sheet
        from .structure import infer_sections
        with self._lock:
            row = self.get(song_id)
            if not row or row.get('active_run_id') != run_id or row.get('status') != 'running':
                return None
            hypotheses = [{'model': model, 'input': 'mixture', 'phrases': basic},
                          *result.get('hypotheses', [])]
            timed = next((h for h in hypotheses if h['model'].startswith('Whisper') and h.get('phrases')), None)
            base = choose_spine(hypotheses) or hypotheses[0]
            selected = (result.get('selected') or {}).get('phrases')
            if not selected:
                selected = consensus(recover_omissions(base['phrases'], hypotheses), [h for h in hypotheses if h is not base])
            keep = bool(row.get('phrase_history'))
            harmony = result.get('harmony') or {}
            has_chords = any(c.get('name') != 'N' for c in harmony.get('events', []))
            run = {'run_id': run_id, 'created_at': _now(), 'model': 'ensemble',
                   'audio_path': row.get('audio', {}).get('path'), 'phrases': selected,
                   'hypotheses': hypotheses, 'harmony': harmony, 'errors': result.get('errors', []),
                   'alignment':(result.get('selected') or {}).get('alignment')}
            changes = {'analysis_runs': [*row.get('analysis_runs', []), run], 'active_run_id': None,
                       'candidate_run_id': run_id if keep else None, 'status': 'review' if selected or keep else 'partial', 'stage': 'review',
                       'analysis_state': 'full_partial' if result.get('errors') else 'full_ready',
                       'analysis_errors': result.get('errors', []), 'review_state': 'needs_review',
                       'stage_label': 'Новый разбор сохранён; ваши правки оставлены' if keep else
                           'Текст не распознан; проверьте запись' if not selected else
                           'Текст и аккорды готовы к проверке' if has_chords else 'Текст готов; аккорды не определены'}
            if not keep:
                chords = harmony.get('events', [])
                changes.update(phrases=selected, words=[w for p in selected for w in p.get('words', [])],
                               selected_run_id=run_id, timing_quality='word_estimated' if timed else 'phrase',
                               sections=infer_sections(selected), chords=chords, harmony=harmony,
                               guitar_sheet=build_guitar_sheet(selected, chords),
                               capabilities={**row.get('capabilities', {}), 'lyrics': bool(selected),
                                             'structure': bool(selected), 'chords': has_chords})
            return self.patch(song_id, changes)

    def edit_phrase(self, song_id, phrase_id, text, note, reviewed, base_revision, action="edit"):
        """Правка/undo/redo одной фразы с проверкой ревизии, без запуска ASR."""
        if base_revision is None:
            raise ValueError("Для сохранения нужна ревизия карточки")
        if action not in {"edit", "undo", "redo"}:
            raise ValueError("Неизвестное действие правки")
        if note not in {"", "unclear", "sung_differently", "vocalization", "sustain"}:
            raise ValueError("Неизвестная пометка фразы")
        with self._lock:
            row = self.get(song_id)
            if not row:
                raise ValueError("Песня не найдена")
            phrases = row.get("phrases", [])
            phrase = next((p for p in phrases if p.get("phrase_id") == phrase_id), None)
            if phrase is None:
                raise ValueError("Фраза не найдена — обновите карточку")
            histories = row.get("phrase_history", {})
            history = histories.get(phrase_id, {"versions": [copy.deepcopy(phrase)], "cursor": 0})
            if action == "edit":
                text = str(text or "").strip()
                if not text or len(text) > 4000:
                    raise ValueError("Введите текст фразы (до 4000 символов) или [неразборчиво]")
                edited = {**phrase, "text": text, "note": note,
                          "review_state": "verified" if reviewed else "needs_review"}
                if text != phrase.get("text"):
                    edited.update(words=[], agreement=[], timing_quality="phrase", alignment_stale=True)
                history["versions"] = history["versions"][:history["cursor"] + 1] + [edited]
                history["cursor"] += 1
            else:
                cursor = history["cursor"] + (1 if action == "redo" else -1)
                if not 0 <= cursor < len(history["versions"]):
                    raise ValueError("Нет правки для повтора" if action == "redo" else "Нет правки для отмены")
                history["cursor"] = cursor
            phrase.clear()
            phrase.update(copy.deepcopy(history["versions"][history["cursor"]]))
            histories[phrase_id] = history
            verified = bool(phrases) and all(p.get("review_state") == "verified" for p in phrases)
            from .chord_layout import build_guitar_sheet
            chords = row.get('chords', [])
            if phrase.get('alignment_stale'):
                for chord in chords:
                    if (chord.get('anchor') or {}).get('phrase_id') == phrase_id:
                        chord['previous_anchor'] = chord.pop('anchor')
                        chord['verified'] = False
            return self.patch(song_id, {"phrases": phrases, "phrase_history": histories,
                "status": "review" if row.get('status') == 'ready' else row.get('status','review'),
                "words": [], "chords": chords, "guitar_sheet": build_guitar_sheet(phrases,chords), "review_state": "lyrics_verified" if verified else "needs_review",
                "stage_label": "Текст проверен; аккорды ещё не проверены" if verified else "Проверьте текст по записи"}, base_revision)

    def delete(self, song_id: str) -> bool:
        """Remove only the owned card metadata; audio cleanup is a separate explicit step."""
        with self._lock:
            if not self._read(song_id):
                return False
            path = self.root / song_id
            # Keep owned audio for the explicit storage-cleanup action; removing
            # a card must not silently destroy a potentially long recording.
            (path / "song.json").unlink(missing_ok=True)
            self._write_json(self.index_path, [x for x in self._read_index() if x != song_id])
            return True
