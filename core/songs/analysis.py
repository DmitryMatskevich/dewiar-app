"""Asynchronous first-pass lyric extraction for a captured song."""
from __future__ import annotations

import threading
import uuid
import math
import json
import os
import copy
import subprocess
import signal
import time
from pathlib import Path


class SongAnalyzer:
    STEP_LABELS = {
        "lyrics": "Распознаём слова",
        "structure": "Размечаем части",
        "chords": "Подбираем аккорды",
    }
    STEP_WORKERS = {
        "lyrics": "whisper,qwen,consensus,align",
        "structure": "structure",
        "chords": "chords",
    }
    MODEL_LABELS = {
        "gigaam": "GigaAM (как в голосовом вводе)",
        "whisper": "Whisper large-v2",
        "qwen": "Qwen3-ASR",
    }

    def __init__(self, root: Path, store, host, emit):
        self.root = Path(root).resolve()
        self.store = store
        self.host = host
        self.emit = emit
        self._lock = threading.RLock()
        self._running: set[str] = set()
        self._cancels = {}

    def running(self, song_id):
        with self._lock:
            return song_id in self._running

    def resume_interrupted(self, song_id=None):
        """Reconnect to detached workers, commit finished work, or restart safely."""
        resumed=[]
        for row in self.store.list(limit=500):
            if song_id and row['song_id'] != song_id:
                continue
            if self.running(row['song_id']):
                continue
            if row.get('status') not in {'interrupted', 'queued'} or not (row.get('audio') or {}).get('path'):
                continue
            old_run=row.get('interrupted_run_id')
            audio_path=(self.root/row['audio']['path']).resolve()
            folder=audio_path.parent.parent/'analysis'/str(old_run) if old_run else None
            if folder and folder.is_dir() and (folder/'gigaam.json').is_file():
                try:
                    baseline=json.loads((folder/'gigaam.json').read_text(encoding='utf-8'))
                    pidfile=folder/'result.pid';pid=int(pidfile.read_text()) if pidfile.is_file() else 0
                    if pid and self._worker_running(pid,old_run):
                        with self._lock:
                            self._running.add(row['song_id']);self._cancels[row['song_id']]=threading.Event()
                        self.store.patch(row['song_id'],{'status':'running','active_run_id':old_run,'interrupted_run_id':None,
                            'stage_label':'Продолжаем фоновое распознавание'})
                        threading.Thread(target=self._watch_detached,args=(row['song_id'],old_run,folder,baseline,pid),
                            name=f"song-resume-{row['song_id'][:8]}",daemon=True).start()
                        resumed.append(row['song_id']);continue
                    # result.json is also a checkpoint: its existence alone is
                    # not evidence that all requested steps have finished.
                    if (folder/'result.json').is_file():
                        result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
                        progress=json.loads((folder/'result.progress.json').read_text(encoding='utf-8')) if (folder/'result.progress.json').is_file() else {}
                        if result.get('completed') or progress.get('label') == 'Разбор завершён; требуется проверка по записи':
                            self._restore_and_save(row['song_id'],old_run,folder,baseline,result)
                            resumed.append(row['song_id']);continue
                except (OSError,ValueError,TypeError):
                    pass
            try:
                self.start(row['song_id'],full=True)
                resumed.append(row['song_id'])
            except (ValueError, OSError) as exc:
                self.store.patch(row['song_id'], {'status': 'failed', 'active_run_id': None,
                    'stage_label': 'Не удалось продолжить: ' + str(exc)[:160]})
                if song_id:
                    raise
                continue
        return resumed

    @staticmethod
    def _worker_running(pid,run_id):
        try:
            os.kill(pid,0)
            cmd=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True,timeout=2)
            return 'worker.py' in cmd and run_id in cmd
        except (OSError,subprocess.SubprocessError):return False

    def _restore_and_save(self,song_id,run_id,folder,baseline,result):
        row=self.store.get(song_id)
        if not row:return
        if row.get('status') in {'interrupted', 'queued'}:
            row=self.store.patch(song_id,{'status':'running','active_run_id':run_id,'interrupted_run_id':None,
                'stage_label':'Сохраняем результат фонового распознавания'})
        saved=self.store.save_full_analysis(song_id,run_id,baseline.get('phrases') or [],
            baseline.get('model') or 'background-worker',result)
        if saved:self.emit('song_analysis',song_id=song_id,running=False,song=saved)

    def _watch_detached(self,song_id,run_id,folder,baseline,pid):
        try:
            deadline=time.monotonic()+3600
            while time.monotonic()<deadline and self._worker_running(pid,run_id):
                if self._cancels.get(song_id) and self._cancels[song_id].is_set():return
                try:
                    label=json.loads((folder/'result.progress.json').read_text())['label']
                    row=self.store.get(song_id)
                    if row and row.get('active_run_id')==run_id and label!=row.get('stage_label'):
                        fresh=self.store.patch(song_id,{'stage_label':label})
                        self.emit('song_analysis',song_id=song_id,running=True,song=fresh)
                except (OSError,ValueError,KeyError,TypeError):pass
                time.sleep(1)
            if self._worker_running(pid, run_id):
                os.kill(pid, signal.SIGTERM)
                raise TimeoutError('Фоновый разбор превысил час; можно повторить обработку')
            output=folder/'result.json'
            if output.is_file():
                result = json.loads(output.read_text(encoding='utf-8'))
                progress = json.loads((folder/'result.progress.json').read_text()) if (folder/'result.progress.json').is_file() else {}
                if not result.get('completed') and progress.get('label') != 'Разбор завершён; требуется проверка по записи':
                    raise RuntimeError('Разбор прервался; промежуточные результаты сохранены, повторите обработку')
                self._restore_and_save(song_id,run_id,folder,baseline,result)
            else:
                raise RuntimeError('Фоновый worker завершился без результата')
        except Exception as exc:
            row=self.store.get(song_id) or {}
            if row.get('active_run_id')==run_id:
                self.store.patch(song_id,{'status':'failed','active_run_id':None,'stage_label':'Фоновый разбор: '+str(exc)[:160]})
        finally:
            cancel = self._cancels.get(song_id)
            if cancel and cancel.is_set() and self._worker_running(pid, run_id):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            with self._lock:self._running.discard(song_id);self._cancels.pop(song_id,None)

    def cancel(self, song_id):
        with self._lock:
            if not self.store.get(song_id):
                raise ValueError("Песня не найдена")
            if song_id in self._cancels:
                self._cancels[song_id].set()
            updated = self.store.patch(song_id, {"status": "cancelled", "active_run_id": None,
                "stage_label": "Отменено; сохранённый текст и запись оставлены"})
            self.emit("song_analysis", song_id=song_id, running=False, song=updated)
            return updated

    def _latest_analysis_folder(self, row):
        """Return the most recent completed worker folder for a song."""
        audio = row.get("audio") or {}
        path = (self.root / str(audio.get("path") or "")).resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError("Сначала сохраните полную запись песни")
        analysis_root = path.parent.parent / "analysis"
        preferred = [row.get("selected_run_id"), row.get("candidate_run_id")]
        preferred.extend(run.get("run_id") for run in reversed(row.get("analysis_runs") or []))
        for run_id in preferred:
            if not run_id:
                continue
            folder = analysis_root / str(run_id)
            if (folder / "result.json").is_file():
                return path, str(run_id), folder
        raise ValueError("Для этого шага нет сохранённого результата; запустите полный разбор")

    def rerun_step(self, song_id, step):
        """Restart one deterministic song stage without discarding other stages."""
        logical = str(step or "").strip().lower()
        if logical not in self.STEP_WORKERS:
            raise ValueError("Этот этап нельзя перезапустить отдельно")
        row = self.store.get(song_id)
        if not row:
            raise ValueError("Песня не найдена")
        if self.running(song_id):
            raise ValueError("Для этой песни уже выполняется другой этап")
        path, base_run_id, folder = self._latest_analysis_folder(row)
        with self._lock:
            if song_id in self._running:
                raise ValueError("Для этой песни уже выполняется другой этап")
            rerun_id = str(uuid.uuid4())
            cancel = threading.Event()
            self._running.add(song_id)
            self._cancels[song_id] = cancel
            updated = self.store.patch(song_id, {
                "status": "running", "active_run_id": rerun_id,
                "stage": logical, "analysis_state": "full_partial",
                "stage_label": f"Перезапускаем этап: {self.STEP_LABELS[logical]}",
            })
            self.emit("song_analysis", song_id=song_id, running=True, song=updated)
            threading.Thread(target=self._run_step,
                             args=(song_id, path, rerun_id, base_run_id, folder, logical, cancel),
                             name=f"song-{logical}-{song_id[:8]}", daemon=True).start()
            return updated

    def _apply_step_result(self, song_id, rerun_id, base_run_id, logical, result):
        from .structure import infer_sections
        from .chord_layout import build_guitar_sheet
        with self.store._lock:
            row = self.store.get(song_id)
            if not row or row.get("active_run_id") != rerun_id:
                return None
            runs = copy.deepcopy(row.get("analysis_runs") or [])
            run = next((item for item in runs if item.get("run_id") == base_run_id), None)
            if run is None:
                raise ValueError("Исходный запуск анализа не найден")
            if result.get("hypotheses"):
                # A step retry may not emit the cached GigaAM baseline again;
                # merge by model so the evidence from earlier stages remains
                # available for review and future retries.
                merged = {item.get("model"): item for item in (run.get("hypotheses") or [])
                          if item.get("model")}
                merged.update({item.get("model"): item for item in result["hypotheses"]
                               if item.get("model")})
                run["hypotheses"] = list(merged.values())
            selected = copy.deepcopy(result.get("selected") or {})
            selected_phrases = selected.get("phrases") or []
            if selected_phrases:
                run["phrases"] = selected_phrases
            harmony = result.get("harmony")
            if harmony is not None:
                run["harmony"] = copy.deepcopy(harmony)
            changes = {
                "analysis_runs": runs, "active_run_id": None, "status": "review",
                "stage": "review", "analysis_state": "full_partial" if result.get("errors") else "full_ready",
                "analysis_errors": copy.deepcopy(result.get("errors") or []),
                "review_state": "needs_review",
            }
            if logical in {"lyrics", "align"} and selected_phrases:
                # Never overwrite a phrase which the user has already edited;
                # the rerun remains available in analysis_runs for comparison.
                # Timing is independent evidence, however: an improved vocal
                # onset must still move a previously edited phrase away from
                # the artificial 0 ms intro while preserving its text.
                if not row.get("phrase_history"):
                    changes.update(
                        phrases=selected_phrases,
                        words=[word for phrase in selected_phrases for word in phrase.get("words", [])],
                        timing_quality="word_estimated" if any(phrase.get("words") for phrase in selected_phrases) else "phrase",
                        sections=infer_sections(selected_phrases),
                    )
                else:
                    current_phrases = copy.deepcopy(row.get("phrases") or [])
                    for index, fresh in enumerate(selected_phrases):
                        if index >= len(current_phrases):
                            break
                        current = current_phrases[index]
                        for key in ("start_ms", "end_ms", "timing_quality", "alignment_warning"):
                            if key in fresh:
                                current[key] = copy.deepcopy(fresh[key])
                        if fresh.get("words") and current.get("words"):
                            # Keep edited word text, but accept improved word
                            # boundaries when the word sequence is unchanged.
                            if len(fresh["words"]) == len(current["words"]):
                                for old_word, new_word in zip(current["words"], fresh["words"]):
                                    for key in ("start_ms", "end_ms", "timing_quality"):
                                        if key in new_word:
                                            old_word[key] = copy.deepcopy(new_word[key])
                    if current_phrases:
                        changes.update(
                            phrases=current_phrases,
                            words=[word for phrase in current_phrases for word in phrase.get("words", [])],
                            timing_quality="word_estimated" if any(phrase.get("words") for phrase in current_phrases) else "phrase",
                            sections=infer_sections(current_phrases),
                        )
                    changes["candidate_run_id"] = base_run_id
            elif logical == "structure":
                phrases = row.get("phrases") or selected_phrases
                changes["sections"] = infer_sections(phrases)
                changes["capabilities"] = {**(row.get("capabilities") or {}), "structure": bool(phrases)}
            elif logical == "chords":
                events = (harmony or {}).get("events") or []
                changes.update(
                    chords=events,
                    harmony=copy.deepcopy(harmony or {}),
                    guitar_sheet=build_guitar_sheet(row.get("phrases") or [], events),
                    capabilities={**(row.get("capabilities") or {}),
                                  "chords": any(event.get("name") != "N" for event in events)},
                )
            changes["stage_label"] = {
                "lyrics": "Распознавание слов обновлено; проверьте текст по записи",
                "structure": "Разметка частей обновлена; проверьте куплеты и припевы",
                "chords": "Проверка аккордов завершена; проверьте результат",
            }[logical]
            return self.store.patch(song_id, changes)

    def _run_step(self, song_id, path, rerun_id, base_run_id, folder, logical, cancel):
        try:
            while not self._heavy_slots.acquire(timeout=.5):
                if cancel.is_set():
                    return
            try:
                from .runtime import analyze
                def progress(label):
                    row = self.store.get(song_id) or {}
                    if row.get("active_run_id") != rerun_id or cancel.is_set():
                        return
                    fresh = self.store.patch(song_id, {"stage_label": label})
                    self.emit("song_analysis", song_id=song_id, running=True, song=fresh)
                result = analyze(path, folder / "result.json", rerun_id, cancel, progress,
                                 steps=self.STEP_WORKERS[logical], resume=True)
                if cancel.is_set():
                    return
                updated = self._apply_step_result(song_id, rerun_id, base_run_id, logical, result)
                if updated:
                    self.emit("song_analysis", song_id=song_id, running=False, song=updated)
            finally:
                self._heavy_slots.release()
        except InterruptedError:
            return
        except Exception as exc:
            row = self.store.get(song_id) or {}
            if row.get("active_run_id") == rerun_id:
                updated = self.store.patch(song_id, {
                    "status": "failed", "active_run_id": None,
                    "analysis_state": "full_partial", "stage": logical,
                    "stage_label": f"Этап не завершён: {str(exc)[:160]}",
                    "analysis_errors": [{"step": logical, "error": str(exc)[:1000]}],
                })
                self.emit("song_analysis", song_id=song_id, running=False, song=updated, error=str(exc))
        finally:
            with self._lock:
                self._running.discard(song_id)
                self._cancels.pop(song_id, None)

    @classmethod
    def normalize_models(cls, models):
        """Return a stable, safe model selection for a song run.

        The singing pipeline always keeps the consensus/alignment stages; the
        selected recognizers only control which independent hypotheses are
        produced.  An omitted selection preserves the historical ensemble.
        """
        if models is None:
            return tuple(cls.MODEL_LABELS)
        if not isinstance(models, (list, tuple, set)):
            raise ValueError("Список распознавателей должен быть массивом")
        result=[]
        for model in models:
            key=str(model or "").strip().lower()
            if key in cls.MODEL_LABELS and key not in result:
                result.append(key)
        if not result:
            raise ValueError("Выберите хотя бы один распознаватель")
        return tuple(result)

    def start(self, song_id: str, full=False, models=None):
        row = self.store.get(song_id)
        if not row:
            raise ValueError("Песня не найдена")
        if row.get("status") == "recording":
            raise ValueError("Сначала остановите запись")
        audio = row.get("audio") or {}
        rel = str(audio.get("path") or "")
        path = (self.root / rel).resolve() if rel else None
        if not path or self.root not in path.parents or not path.is_file():
            raise ValueError("Сначала сохраните полную запись песни")
        selected_models = self.normalize_models(models) if full else ("gigaam",)
        with self._lock:
            if song_id in self._running:
                raise ValueError("Распознавание уже идёт")
            self._running.add(song_id)
            self._cancels[song_id] = threading.Event()
            run_id = str(uuid.uuid4())
            try:
                labels = ", ".join(self.MODEL_LABELS[key] for key in selected_models)
                updated = self.store.patch(song_id, {"status": "running", "analysis_state": "transcribing", "stage": "lyrics",
                                                     "active_run_id": run_id, "analysis_models": list(selected_models),
                                                     "stage_label": f"Распознаём слова: {labels}"})
                self.emit("song_analysis", song_id=song_id, running=True, song=updated)
                args = (song_id, path, run_id, True, selected_models) if full else (song_id, path, run_id, False, selected_models)
                threading.Thread(target=self._run, args=args, name=f"song-lyrics-{song_id[:8]}", daemon=True).start()
            except Exception:
                self._running.discard(song_id)
                self._cancels.pop(song_id, None)
                raise
            return updated

    # Full song passes use an external worker and must not serialize unrelated
    # cards.  Keep a small bounded slot pool so two independent songs can run
    # together while still protecting the machine from an unbounded model fanout.
    _heavy_slots = threading.BoundedSemaphore(max(2, min(8, int(os.environ.get('DEWIAR_SONG_ANALYSIS_PARALLEL', '2')))))

    def _run(self, song_id: str, path: Path, run_id: str, full=False, models=("gigaam",)):
        try:
            cancel = self._cancels.get(song_id)
            if cancel is not None and cancel.is_set():
                return
            selected_stt = None
            basic_error = None
            try:
                use_gigaam = "gigaam" in models
                current_fn = getattr(self.host, "current_stt", None)
                if use_gigaam and callable(current_fn):
                    selected_stt = current_fn()
                    if selected_stt is None or not getattr(selected_stt, "ready", True):
                        # `--text-only` deliberately skips microphone startup, so
                        # the normal background STT loader is absent.  Reuse a
                        # cached GigaAM model for file analysis without silently
                        # downloading weights from the network.
                        settings = getattr(self.host, "s", None)
                        stt_cfg = settings.get("stt") if settings is not None and hasattr(settings, "get") else {}
                        if (stt_cfg or {}).get("engine") == "gigaam":
                            from stt import GigaAMSTT
                            name = (stt_cfg or {}).get("gigaam_model") or "gigaam-v3-e2e-rnnt"
                            selected_stt = getattr(self.host, "gigaam", None)
                            if selected_stt is None or getattr(selected_stt, "model_name", "") != name:
                                selected_stt = GigaAMSTT(name)
                                selected_stt.mirror = None
                            if not selected_stt.load_cached():
                                raise RuntimeError(selected_stt.error or "Локальная модель не загружена; скачивание отключено")
                        else:
                            raise RuntimeError("локальный распознаватель ещё готовится — повторите после его загрузки")
                phrases = []
                if use_gigaam:
                    # Не переключать провайдера распознавания за спиной
                    # пользователя.  This baseline is optional: a Whisper or
                    # Qwen-only run must not silently invoke the voice-input
                    # recognizer as a fourth model.
                    kwargs = {"allow_fallback": False, "cancel": self._cancels.get(song_id), "stt": selected_stt}
                    if cancel is not None and cancel.is_set():
                        return
                    segments = self.host.transcribe_file(path, **kwargs)
                    for start, end, text in segments or []:
                        text = str(text or "").strip()
                        if not text:
                            continue
                        if not all(math.isfinite(float(x)) for x in (start, end)) or float(end) <= float(start) or float(start) < 0:
                            raise ValueError("Распознаватель вернул некорректные времена фраз")
                        start_ms, end_ms = int(float(start) * 1000), int(float(end) * 1000)
                        phrases.append({"phrase_id": f"{run_id}-{len(phrases)}", "start_ms": start_ms, "end_ms": end_ms,
                                        "text": text, "kind": "unknown", "timing_quality": "phrase", "words": [],
                                        "review_state": "needs_review", "note": ""})
                    model = getattr(selected_stt, "model_name", None) or type(selected_stt).__name__
                else:
                    model = "not-selected"
            except Exception as exc:
                if not full:
                    raise
                phrases = []
                model = getattr(selected_stt, 'model_name', None) or 'local-stt-unavailable'
                basic_error = {'step': 'local_stt', 'error': str(exc)[:1000]}
            if full:
                from .runtime import analyze
                def progress(label):
                    with self.store._lock:
                        row = self.store.get(song_id) or {}
                        if row.get('active_run_id') != run_id or cancel.is_set():
                            return
                        fresh = self.store.patch(song_id, {'stage_label': label})
                    self.emit('song_analysis', song_id=song_id, running=True, song=fresh)
                progress('Базовый текст недоступен; запускаем выбранные модели' if basic_error else 'Базовый текст сохранён; запускаем выбранные модели')
                folder = path.parent.parent / 'analysis' / run_id
                folder.mkdir(parents=True, exist_ok=True)
                import json
                (folder/'gigaam.json').write_text(json.dumps({'model': model, 'phrases': phrases}, ensure_ascii=False))
                while not self._heavy_slots.acquire(timeout=.5):
                    if cancel.is_set():
                        return
                try:
                    if cancel.is_set():
                        return
                    steps = [key for key in ('whisper', 'qwen') if key in models]
                    # Consensus can use the selected GigaAM baseline, a single
                    # heavy recognizer, or both.  Keeping it in every full run
                    # makes the result comparable and leaves raw hypotheses.
                    steps += ['consensus', 'align', 'chords']
                    result = analyze(path, folder/'result.json', run_id, cancel, progress,
                                     steps=','.join(steps))
                except InterruptedError:
                    return
                except Exception as exc:
                    result = {'hypotheses': [], 'errors': [{'step': 'runtime', 'error': str(exc)}]}
                finally:
                    self._heavy_slots.release()
                if basic_error:
                    result.setdefault('errors', []).insert(0, basic_error)
                updated = self.store.save_full_analysis(song_id, run_id, phrases, model, result)
            else:
                updated = self.store.save_analysis(song_id, run_id, phrases, model)
            if updated:
                self.emit("song_analysis", song_id=song_id, running=False, song=updated)
        except Exception as exc:  # noqa: BLE001 — ошибка видна в карточке
            with self.store._lock:
                row = self.store.get(song_id) or {}
                if row.get("active_run_id") != run_id:
                    return
                updated = self.store.patch(song_id, {"status": "failed", "active_run_id": None, "analysis_state": "lyrics_error", "stage": "lyrics",
                                                 "stage_label": "Ошибка распознавания: " + str(exc)[:160],
                                                 "sources": [*(row.get("sources") or []), {"kind": "error", "text": str(exc)[:500]}]})
            self.emit("song_analysis", song_id=song_id, running=False, song=updated, error=str(exc))
        finally:
            with self._lock:
                self._running.discard(song_id)
                self._cancels.pop(song_id, None)
