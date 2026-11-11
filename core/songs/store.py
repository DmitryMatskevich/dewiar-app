"""Atomic local storage for the Songs studio tab.

Song records are user data.  Writes use a temporary file and replace so a
crash cannot leave a half-written song card.  Audio ownership is represented
explicitly; deleting a card removes its song folder and generated artifacts,
but never follows or deletes an external user path.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import re
import shutil
import tempfile
import threading
import uuid
from pathlib import Path


STAGES = ("identify", "sources", "record", "lyrics", "text_reference", "structure", "chords", "karaoke", "review")
STATUS = {"queued", "recording", "running", "needs_input", "review", "ready", "partial", "failed", "cancelled", "interrupted"}
TAG_OPTIONS = ("Компания", "Мужская", "Любовь", "Шансон", "Бард", "Грусть", "Юмор", "Дворовые", "Народные", "Рок")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _clean(value: object, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def clean_tags(value: object) -> list[str]:
    """Normalize and retain built-in and user-created song tags."""
    if not isinstance(value, (list, tuple, set)):
        return []
    tags = []
    seen = set()
    for item in value:
        tag = _clean(item, 40)
        key = tag.casefold()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
        if len(tags) >= 32:
            break
    return tags


class SongStore:
    schema_version = 1

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()
        self._card_cache = {}

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

    def exists(self, song_id: str) -> bool:
        """Validate identity/existence without loading recognition history."""
        return self._path(song_id).is_file()

    def _card(self, song_id):
        path = self._path(song_id)
        try:
            stat = path.stat()
        except OSError:
            self._card_cache.pop(song_id, None)
            return None
        signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        cached = self._card_cache.get(song_id)
        if cached and cached[0] == signature:
            return cached[1]
        row = self._read(song_id)
        if not row:
            self._card_cache.pop(song_id, None)
            return None
        fields = {'song_id', 'title', 'search_query', 'artist', 'rating', 'tags', 'tag_scores', 'status', 'stage', 'stage_label',
                  'capabilities', 'audio', 'phrases', 'analysis_state',
                  'source_candidates', 'selected_source_id', 'updated_at', 'created_at',
                  'revision', 'source', 'source_ref', 'recording_version'}
        card = {key: row[key] for key in fields if key in row}
        self._card_cache[song_id] = (signature, card)
        return card

    @staticmethod
    def _lyrics_haystack(row: dict) -> str:
        """Text-only index for searching phrases without model hypotheses."""
        chunks = []
        for phrase in row.get("phrases") or []:
            if not isinstance(phrase, dict):
                continue
            for key in ("text", "display_text", "raw_text"):
                value = phrase.get(key)
                if isinstance(value, str):
                    chunks.append(value)
            for word in phrase.get("words") or []:
                if isinstance(word, dict):
                    value = word.get("text") or word.get("word")
                    if isinstance(value, str):
                        chunks.append(value)
        for word in row.get("words") or []:
            if isinstance(word, dict):
                value = word.get("text") or word.get("word")
                if isinstance(value, str):
                    chunks.append(value)
        return " ".join(chunks).casefold()

    def list(self, query: str = "", status: str = "", limit: int = 100, compact: bool = False, tag: str | list[str] = "", lyrics_query: str = "", rating: int | str = "", tags: list[str] | None = None) -> list[dict]:
        query = _clean(query, 120).casefold()
        raw_tags = tags if tags is not None else tag
        if isinstance(raw_tags, (list, tuple, set)):
            tag_filter = {_clean(item, 40) for item in raw_tags if _clean(item, 40)}
        else:
            cleaned_tag = _clean(raw_tags, 40)
            tag_filter = {cleaned_tag} if cleaned_tag else set()
        lyrics_query = _clean(lyrics_query, 240).casefold()
        rating_filter = None
        if rating not in (None, "", 0, "0"):
            try:
                parsed_rating = int(rating)
            except (TypeError, ValueError):
                parsed_rating = -1
            if 1 <= parsed_rating <= 5:
                rating_filter = parsed_rating
            else:
                return []
        with self._lock:
            rows = []
            for song_id in reversed(self._read_index()):
                # A lyrics query may need the legacy top-level ``words`` field,
                # which is intentionally omitted from compact cards.
                row = self._read(song_id) if (not compact or lyrics_query) else self._card(song_id)
                if not row:
                    continue
                haystack = f"{row.get('title', '')} {row.get('artist', '')} {' '.join(row.get('tags') or [])}".casefold()
                if query and query not in haystack:
                    continue
                if lyrics_query and lyrics_query not in self._lyrics_haystack(row):
                    continue
                selected_statuses = {
                    "running": {"queued", "recording", "running"},
                    # The batch view contains jobs that can still be resumed
                    # or are currently being processed in the background,
                    # including jobs that stopped and await a retry.
                    "batch": {"queued", "recording", "running", "needs_input",
                               "interrupted", "failed", "cancelled", "partial"},
                    # A separate view makes interrupted/failed work visible
                    # instead of leaving it mixed into the main library.
                    "stopped": {"interrupted", "failed", "cancelled", "partial"},
                }.get(status, {status})
                if status and row.get("status") not in selected_statuses:
                    continue
                if tag_filter and not tag_filter.intersection(row.get("tags") or []):
                    continue
                if rating_filter is not None and int(row.get("rating") or 0) != rating_filter:
                    continue
                if compact:
                    # The songs list only needs card/progress metadata. Avoid
                    # copying hypotheses, words and guitar sheets while an
                    # analysis worker is writing large song records.
                    fields = {'song_id', 'title', 'search_query', 'artist', 'rating', 'tags', 'tag_scores', 'status', 'stage', 'stage_label',
                              'capabilities', 'audio', 'phrases', 'analysis_state',
                              'source_candidates', 'selected_source_id', 'updated_at', 'created_at',
                              'revision', 'source', 'source_ref', 'recording_version'}
                    rows.append({key: copy.deepcopy(row[key]) for key in fields if key in row})
                else:
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
            # Keep the exact query separately from the editable display title.
            # Existing cards without this field continue to fall back to title.
            "search_query": title if source == "query" else "",
            "artist": artist,
            "rating": 0,
            "tags": [],
            "tag_scores": {},
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
            allowed = {"title", "search_query", "artist", "rating", "tags", "tag_scores", "source", "source_ref", "recording_version", "status", "analysis_state", "review_state",
                       "stage", "stage_label", "coverage", "audio", "capabilities", "sections", "phrases", "words", "sources", "chords", "guitar_sheet",
                       "analysis_runs", "phrase_history", "active_run_id", "interrupted_run_id", "selected_run_id", "candidate_run_id", "timing_quality",
                       "harmony", "analysis_errors", "analysis_models", "source_candidates", "selected_source_id", "capture_result", "text_reference",
                       "vocal_activity", "timing_version"}
            if "title" in changes and not _clean(changes["title"]):
                raise ValueError("Укажите название песни")
            if "status" in changes and changes["status"] not in STATUS:
                raise ValueError("Неизвестное состояние песни")
            for key, value in changes.items():
                if key in allowed:
                    if key in {"title", "search_query", "artist"}:
                        row[key] = _clean(value)
                    elif key == "rating":
                        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
                            raise ValueError("Оценка песни должна быть от 0 до 5 звёзд")
                        row[key] = value
                    elif key == "tags":
                        row[key] = clean_tags(value)
                    elif key == "tag_scores":
                        row[key] = {tag: float(score) for tag, score in (value or {}).items() if tag in TAG_OPTIONS and isinstance(score, (int, float))}
                    else:
                        row[key] = copy.deepcopy(value)
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
        from .tags import infer_tags
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
            tags, tag_scores = infer_tags(selected, row.get('title', ''), row.get('artist', ''))
            tags = list(dict.fromkeys([*tags, *(tag for tag in row.get('tags', []) if tag not in TAG_OPTIONS)]))
            changes.update(tags=tags, tag_scores=tag_scores)
            return self.patch(song_id, changes)

    def save_full_analysis(self, song_id, run_id, basic, model, result):
        from .consensus import consensus, choose_spine, recover_omissions
        from .chord_layout import build_guitar_sheet
        from .structure import infer_sections
        from .tags import infer_tags
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
            selected = copy.deepcopy(selected)
            for index, phrase in enumerate(selected):
                phrase.setdefault('phrase_id', f'{run_id}-{index}')
            keep = bool(row.get('phrase_history'))
            harmony = result.get('harmony') or {}
            has_chords = any(c.get('name') != 'N' for c in harmony.get('events', []))
            run = {'run_id': run_id, 'created_at': _now(), 'model': 'ensemble',
                   'audio_path': row.get('audio', {}).get('path'), 'phrases': selected,
                   'hypotheses': hypotheses, 'harmony': harmony, 'errors': result.get('errors', []),
                   'comparison': copy.deepcopy(result.get('comparison', {})),
                   'text_reference': copy.deepcopy(result.get('text_reference', {})),
                   'alignment':(result.get('selected') or {}).get('alignment')}
            changes = {'analysis_runs': [*row.get('analysis_runs', []), run], 'active_run_id': None,
                       'candidate_run_id': run_id if keep else None, 'status': 'review' if selected or keep else 'partial', 'stage': 'review',
                       'analysis_state': 'full_partial' if result.get('errors') else 'full_ready',
                       'analysis_errors': result.get('errors', []), 'review_state': 'needs_review',
                       'stage_label': 'Новый разбор сохранён; ваши правки оставлены' if keep else
                           'Текст не распознан; проверьте запись' if not selected else
                           'Текст и аккорды готовы к проверке' if has_chords else 'Текст готов; аккорды не определены'}
            if 'text_reference' in result:
                from .text_reference import result_patch
                changes.update(result_patch(row,result['text_reference']))
            activity = result.get('vocal_activity') if 'vocal_activity' in result else row.get('vocal_activity')
            if 'vocal_activity' in result:
                from .vocal_timing import VERSION as timing_version
                run['vocal_activity'] = copy.deepcopy(result['vocal_activity'])
                changes.update(vocal_activity=copy.deepcopy(result['vocal_activity']), timing_version=timing_version)
            if keep and result.get('vocal_activity'):
                # The user's text and manual boundaries stay; only edges that
                # the new vocal map shows inside a break are moved.
                from .vocal_timing import fit_phrases
                fitted, report = fit_phrases(row.get('phrases') or [], result['vocal_activity'])
                if report.get('applied'):
                    sections = copy.deepcopy(row.get('sections') or [])
                    for section in sections:
                        members = [fitted[i] for i in section.get('phrase_indexes', []) if 0 <= i < len(fitted)]
                        if members:
                            section.update(start_ms=min(p['start_ms'] for p in members),
                                           end_ms=max(p['end_ms'] for p in members))
                    changes.update(phrases=fitted, words=[w for p in fitted for w in p.get('words', [])],
                                   sections=sections, stage_label='Новый разбор сохранён; ваши правки оставлены, '
                                   'начала и концы строк сверены с голосом')
            if not keep:
                chords = harmony.get('events', [])
                changes.update(phrases=selected, words=[w for p in selected for w in p.get('words', [])],
                               selected_run_id=run_id, timing_quality='word_estimated' if timed else 'phrase',
                               sections=infer_sections(selected), chords=chords, harmony=harmony,
                               guitar_sheet=build_guitar_sheet(selected, chords, None, activity),
                               capabilities={**row.get('capabilities', {}), 'lyrics': bool(selected),
                                             'structure': bool(selected), 'chords': has_chords})
            tags, tag_scores = infer_tags(selected, row.get('title', ''), row.get('artist', ''))
            tags = list(dict.fromkeys([*tags, *(tag for tag in row.get('tags', []) if tag not in TAG_OPTIONS)]))
            changes.update(tags=tags, tag_scores=tag_scores)
            return self.patch(song_id, changes)

    def edit_phrase(self, song_id, phrase_id, text, note, reviewed, base_revision, action="edit", word_start=None,
                    word_start_ms=None, word_end_ms=None):
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
                if word_start is not None:
                    if not isinstance(word_start, int) or isinstance(word_start, bool):
                        raise ValueError("Не удалось определить слово для правки")
                    words = copy.deepcopy(phrase.get("words") or [])
                    target = next((w for w in words if w.get("char_start") == word_start), None)
                    if target is None:
                        token = next((m for m in re.finditer(r"\S+", str(phrase.get("text") or ""))
                                      if m.start() == word_start), None)
                        if token is None:
                            raise ValueError("Выбранное слово не найдено во фразе — обновите лист песни")
                        start_ms, end_ms = word_start_ms, word_end_ms
                        if (not isinstance(start_ms, (int, float)) or isinstance(start_ms, bool) or
                                not isinstance(end_ms, (int, float)) or isinstance(end_ms, bool) or
                                not math.isfinite(start_ms) or not math.isfinite(end_ms) or
                                start_ms < phrase.get("start_ms", 0) or end_ms > phrase.get("end_ms", 0) or
                                end_ms <= start_ms):
                            raise ValueError("Не удалось сохранить время выбранного слова — выберите его при воспроизведении")
                        target = {"text": token.group(), "char_start": token.start(), "char_end": token.end(),
                                  "start_ms": round(start_ms), "end_ms": round(end_ms),
                                  "method": "manual_word_edit_estimated"}
                        words.append(target)
                    if not text or len(text) > 120 or any(char.isspace() for char in text):
                        raise ValueError("Введите одно слово без пробелов (до 120 символов)")
                    old_start, old_end = target["char_start"], target["char_end"]
                    delta = len(text) - (old_end - old_start)
                    phrase_text = phrase["text"][:old_start] + text + phrase["text"][old_end:]
                    for word in words:
                        if word is target:
                            word.update(text=text, char_end=old_start + len(text),
                                        method="manual_word_edit_estimated" if word.get("method") == "manual_word_edit_estimated" else "manual_word_edit",
                                        verified=True)
                        elif word.get("char_start", -1) >= old_end:
                            word["char_start"] += delta
                            word["char_end"] += delta
                    agreement = copy.deepcopy(phrase.get("agreement") or [])
                    for vote in agreement:
                        if vote.get("char_start") == old_start:
                            vote.update(text=text, char_end=old_start + len(text), needs_review=False)
                        elif vote.get("char_start", -1) >= old_end:
                            vote["char_start"] += delta
                            vote["char_end"] += delta
                    edited = {**phrase, "text": phrase_text, "words": words, "agreement": agreement,
                              "note": note, "review_state": "verified" if reviewed else "needs_review"}
                    for chord in row.get("chords", []):
                        anchor = chord.get("anchor") or {}
                        at = anchor.get("grapheme_index")
                        if anchor.get("phrase_id") == phrase_id and isinstance(at, int) and at >= old_start:
                            chord["previous_anchor"] = chord.pop("anchor")
                            chord["verified"] = False
                else:
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
                "words": [w for p in phrases for w in p.get('words',[])], "chords": chords, "guitar_sheet": build_guitar_sheet(phrases,chords), "review_state": "lyrics_verified" if verified else "needs_review",
                "stage_label": "Текст проверен; аккорды ещё не проверены" if verified else "Проверьте текст по записи"}, base_revision)

    def edit_display_line(self, song_id, line_id, text, reviewed, base_revision):
        """Edit one displayed lyric row even when it joins adjacent timed ASR phrases."""
        if base_revision is None:
            raise ValueError("Для сохранения нужна ревизия карточки")
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text or len(text) > 4000:
            raise ValueError("Введите текст строки (до 4000 символов) или [неразборчиво]")
        with self._lock:
            row = self.get(song_id)
            if not row:
                raise ValueError("Песня не найдена")
            if row.get('revision') != base_revision:
                raise ValueError("Карточка уже изменилась — обновите песню и повторите правку")
            from .public import public_song
            display_line = next((line for line in public_song(row).get('guitar_sheet', [])
                                 if line.get('phrase_id') == line_id and line.get('text')), None)
            spans = (display_line or {}).get('source_spans') or []
            if not display_line or len(spans) < 2:
                raise ValueError("Строка уже изменилась — обновите лист песни")
            phrase_by_id = {phrase.get('phrase_id'): phrase for phrase in row.get('phrases', [])}
            source_ids = []
            for span in spans:
                phrase_id = span.get('phrase_id')
                if not phrase_id or phrase_id not in phrase_by_id:
                    raise ValueError("Исходные части строки изменились — обновите лист песни")
                if phrase_id not in source_ids:
                    source_ids.append(phrase_id)
                original = phrase_by_id[phrase_id].get('text', '')
                a, b = span.get('phrase_char_start'), span.get('phrase_char_end')
                da, db = span.get('display_char_start'), span.get('display_char_end')
                if not all(isinstance(value, int) and not isinstance(value, bool) for value in (a, b, da, db)):
                    raise ValueError("Не удалось проверить границы строки — обновите лист песни")
                if not (0 <= a < b <= len(original) and 0 <= da < db <= len(display_line['text'])):
                    raise ValueError("Границы строки уже изменились — обновите лист песни")
                if original[a:b] != display_line['text'][da:db]:
                    raise ValueError("Текст строки уже изменился — обновите лист песни")
            if len(source_ids) < 2:
                raise ValueError("Строка уже изменилась — обновите лист песни")
            tokens = list(re.finditer(r"\S+", text))
            if len(tokens) < len(spans):
                raise ValueError("Оставьте хотя бы по одному слову в каждой временной части строки")
            weights = [max(1, span['display_char_end'] - span['display_char_start']) for span in spans]
            total = sum(weights)
            cuts = [0]
            cumulative = 0
            for index in range(1, len(spans)):
                cumulative += weights[index - 1]
                target = len(text) * cumulative / total
                low, high = cuts[-1] + 1, len(tokens) - (len(spans) - index)
                chosen = min(range(low, high + 1), key=lambda i: abs(tokens[i].start() - target))
                cuts.append(chosen)
            cuts.append(len(tokens))
            chunks = [text[tokens[a].start():tokens[b - 1].end()] for a, b in zip(cuts, cuts[1:])]
            phrases = row.get('phrases', [])
            histories = row.get('phrase_history', {})
            for span, chunk in zip(spans, chunks):
                phrase_id = span['phrase_id']
                phrase = phrase_by_id[phrase_id]
                a, b = span['phrase_char_start'], span['phrase_char_end']
                new_phrase_text = phrase['text'][:a] + chunk + phrase['text'][b:]
                updated = {**phrase, 'text': new_phrase_text,
                           'review_state': 'verified' if reviewed else 'needs_review'}
                if new_phrase_text != phrase['text']:
                    updated.update(words=[], agreement=[], timing_quality='phrase', alignment_stale=True)
                    for chord in row.get('chords', []):
                        anchor = chord.get('anchor') or {}
                        at = anchor.get('grapheme_index')
                        if anchor.get('phrase_id') == phrase_id and isinstance(at, int) and at >= a:
                            chord['previous_anchor'] = chord.pop('anchor')
                            chord['verified'] = False
                history = histories.get(phrase_id, {'versions': [copy.deepcopy(phrase)], 'cursor': 0})
                if (updated.get('text') != phrase.get('text') or
                        updated.get('review_state') != phrase.get('review_state')):
                    history['versions'] = history['versions'][:history['cursor'] + 1] + [updated]
                    history['cursor'] += 1
                    histories[phrase_id] = history
                    phrase.clear()
                    phrase.update(copy.deepcopy(updated))
            verified = bool(phrases) and all(phrase.get('review_state') == 'verified' for phrase in phrases)
            from .chord_layout import build_guitar_sheet
            chords = row.get('chords', [])
            return self.patch(song_id, {'phrases': phrases, 'phrase_history': histories,
                'words': [word for phrase in phrases for word in phrase.get('words', [])], 'chords': chords,
                'guitar_sheet': build_guitar_sheet(phrases, chords),
                'status': 'review' if row.get('status') == 'ready' else row.get('status', 'review'),
                'review_state': 'lyrics_verified' if verified else 'needs_review',
                'stage_label': 'Текст проверен; аккорды ещё не проверены' if verified else 'Проверьте текст по записи'},
                base_revision)

    def delete(self, song_id: str) -> bool:
        """Remove a card and every file stored under its owned song folder.

        Imported/recorded audio and generated karaoke/analysis artifacts are
        stored below ``root/<song_id>``.  An external file referenced by a card
        is never touched.
        """
        with self._lock:
            if not self._read(song_id):
                return False
            root = self.root.resolve()
            path = (self.root / song_id).resolve()
            if path.parent != root or path == root:
                raise ValueError("Недопустимая папка песни")
            shutil.rmtree(path)
            self._card_cache.pop(song_id, None)
            self._write_json(self.index_path, [x for x in self._read_index() if x != song_id])
            return True
