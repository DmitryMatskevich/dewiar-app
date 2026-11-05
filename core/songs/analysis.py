"""Asynchronous first-pass lyric extraction for a captured song."""
from __future__ import annotations

import threading
import uuid
import math
import json
import os
import subprocess
import signal
import time
from pathlib import Path


class SongAnalyzer:
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

    def start(self, song_id: str, full=False):
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
        with self._lock:
            if song_id in self._running:
                raise ValueError("Распознавание уже идёт")
            self._running.add(song_id)
            self._cancels[song_id] = threading.Event()
            run_id = str(uuid.uuid4())
            try:
                updated = self.store.patch(song_id, {"status": "running", "analysis_state": "transcribing", "stage": "lyrics",
                                                     "active_run_id": run_id, "stage_label": "Распознаём слова"})
                self.emit("song_analysis", song_id=song_id, running=True, song=updated)
                args = (song_id, path, run_id, True) if full else (song_id, path, run_id)
                threading.Thread(target=self._run, args=args, name=f"song-lyrics-{song_id[:8]}", daemon=True).start()
            except Exception:
                self._running.discard(song_id)
                self._cancels.pop(song_id, None)
                raise
            return updated

    _heavy_lock = threading.Lock()

    def _run(self, song_id: str, path: Path, run_id: str, full=False):
        try:
            cancel = self._cancels.get(song_id)
            if cancel is not None and cancel.is_set():
                return
            selected_stt = None
            basic_error = None
            try:
                current_fn = getattr(self.host, "current_stt", None)
                if callable(current_fn):
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
                # Не переключать провайдера распознавания за спиной пользователя.
                kwargs = {"allow_fallback": False, "cancel": self._cancels.get(song_id)}
                if selected_stt is not None:
                    kwargs["stt"] = selected_stt
                if cancel is not None and cancel.is_set():
                    return
                segments = self.host.transcribe_file(path, **kwargs)
                phrases = []
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
                progress('Базовый текст недоступен; запускаем остальные модели' if basic_error else 'Базовый текст сохранён; ждём модуль полного разбора')
                folder = path.parent.parent / 'analysis' / run_id
                folder.mkdir(parents=True, exist_ok=True)
                import json
                (folder/'gigaam.json').write_text(json.dumps({'model': model, 'phrases': phrases}, ensure_ascii=False))
                while not self._heavy_lock.acquire(timeout=.5):
                    if cancel.is_set():
                        return
                try:
                    if cancel.is_set():
                        return
                    result = analyze(path, folder/'result.json', run_id, cancel, progress)
                except InterruptedError:
                    return
                except Exception as exc:
                    result = {'hypotheses': [], 'errors': [{'step': 'runtime', 'error': str(exc)}]}
                finally:
                    self._heavy_lock.release()
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
