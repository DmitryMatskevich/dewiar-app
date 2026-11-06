"""Local preparation of instrumental candidates; keeps job state out of song.json."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


class InstrumentalManager:
    """Owns separation jobs and their files under the Songs data directory."""

    # Separation is also a worker process.  Allow independent songs to be
    # prepared concurrently, with a bounded default rather than a global lock.
    _heavy_slots = threading.BoundedSemaphore(max(2, min(8, int(os.environ.get('DEWIAR_SONG_SEPARATION_PARALLEL', '2')))))

    def __init__(self, root: Path, store, emit):
        self.root = Path(root).resolve()
        self.store = store
        self.emit = emit
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self.recover()

    def _folder(self, song_id):
        # Store validates IDs on get; resolve again before any filesystem operation.
        if not self.store.get(song_id):
            raise ValueError("Песня не найдена")
        path = (self.root / song_id / "karaoke").resolve()
        if self.root not in path.parents:
            raise ValueError("Недопустимая папка минусовки")
        return path

    def _manifest(self, song_id):
        path = self._folder(song_id) / "manifest.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) and value.get("schema_version") == 1 else {"schema_version": 1, "revision": 0, "candidates": []}
        except (OSError, ValueError):
            return {"schema_version": 1, "revision": 0, "candidates": []}

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with tmp.open("x", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def get(self, song_id):
        with self._lock:
            return self._manifest(song_id)

    def summary(self, song_id):
        m = self.get(song_id)
        job = m.get("active_job") or {}
        selected = next((c for c in m.get("candidates", [])
                         if c.get("candidate_id") == m.get("selected_candidate")), None)
        artifact = None
        if selected:
            path = self.path_for(song_id)
            if path:
                artifact = {"name": path.name, "bytes": path.stat().st_size,
                            "seconds": selected.get("seconds"),
                            "sample_rate": selected.get("sample_rate")}
        return {"state": job.get("state", "ready" if m.get("selected_candidate") else "absent"),
                "stage": job.get("stage"), "progress": job.get("progress"),
                "error": job.get("error"),
                "selected_candidate": m.get("selected_candidate"),
                "quality_status": selected.get("quality_status") if selected else None,
                "artifact": artifact}

    def running(self, song_id):
        with self._lock:
            thread = self._threads.get(song_id)
            return bool(thread and thread.is_alive())

    def prepare_pending(self):
        """Queue every owned recording which has no usable instrumental yet.

        This is deliberately independent of lyric review: a karaoke worker can
        run while text, sections, or chords still need checking.  The method is
        safe to call at startup and after a new recording is attached.
        """
        result = {"started": [], "ready": [], "already_running": [], "missing_audio": [], "errors": []}
        for row in self.store.list(limit=500):
            song_id = str(row.get("song_id") or "")
            audio = row.get("audio") or {}
            source = (self.root / str(audio.get("path") or "")).resolve()
            if self.root not in source.parents or not source.is_file():
                result["missing_audio"].append(song_id)
                continue
            summary = self.summary(song_id)
            if summary.get("state") == "ready" and summary.get("artifact"):
                result["ready"].append(song_id)
                continue
            if self.running(song_id):
                result["already_running"].append(song_id)
                continue
            try:
                started = self.start(song_id)
                result["ready" if started.get("reused") else "started"].append(song_id)
            except Exception as exc:  # one card must not block the rest
                result["errors"].append({"song_id": song_id, "error": str(exc)[:240]})
        return result

    def recover(self):
        for manifest_path in self.root.glob("*/karaoke/manifest.json"):
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                job = m.get("active_job") or {}
                if job.get("state") in {"queued", "preparing", "separating", "checking"}:
                    job.update(state="interrupted", stage="interrupted", error="Приложение завершилось во время подготовки")
                    m["active_job"] = job
                    m["revision"] = int(m.get("revision", 0)) + 1
                    self._write(manifest_path, m)
            except (OSError, ValueError, TypeError):
                continue

    def start(self, song_id, force=False):
        row = self.store.get(song_id)
        if not row:
            raise ValueError("Песня не найдена")
        audio = row.get("audio") or {}
        source = (self.root / str(audio.get("path") or "")).resolve()
        if self.root not in source.parents or not source.is_file():
            raise ValueError("Сначала добавьте полную запись песни")
        digest = hashlib.sha256()
        with source.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        source_hash = digest.hexdigest()
        with self._lock:
            if song_id in self._threads and self._threads[song_id].is_alive():
                raise ValueError("Минусовка уже готовится")
            m = self._manifest(song_id)
            if not force and m.get("source", {}).get("sha256") == source_hash:
                selected = next((c for c in m.get("candidates", []) if c.get("candidate_id") == m.get("selected_candidate")), None)
                if selected and (self._folder(song_id) / selected.get("artifact", "")).is_file():
                    return {"song_id": song_id, "reused": True, "manifest": m}
            run_id = str(uuid.uuid4())
            m.update(source={"sha256": source_hash, "bytes": source.stat().st_size},
                     active_job={"run_id": run_id, "state": "queued", "stage": "queued", "progress": 0})
            m["revision"] = int(m.get("revision", 0)) + 1
            self._write(self._folder(song_id) / "manifest.json", m)
            self._cancelled.discard(run_id)
            thread = threading.Thread(target=self._run, args=(song_id, run_id, source, source_hash),
                                      name="song-instrumental-" + song_id[:8], daemon=True)
            self._threads[song_id] = thread
            thread.start()
            self.emit("song_instrumental", song_id=song_id, summary=self.summary(song_id))
            return {"song_id": song_id, "run_id": run_id, "reused": False, "manifest": m}

    def cancel(self, song_id):
        with self._lock:
            m = self._manifest(song_id)
            job = m.get("active_job") or {}
            run_id = job.get("run_id")
            if not run_id or not self._threads.get(song_id) or not self._threads[song_id].is_alive():
                raise ValueError("Для этой песни нет активной подготовки минусовки")
            self._cancelled.add(run_id)
            process = self._processes.get(run_id)
            if process and process.poll() is None:
                process.terminate()
            job.update(state="cancelled", stage="cancelled", progress=None)
            m["active_job"] = job
            m["revision"] = int(m.get("revision", 0)) + 1
            self._write(self._folder(song_id) / "manifest.json", m)
        self.emit("song_instrumental", song_id=song_id, summary=self.summary(song_id))
        return m

    def _run(self, song_id, run_id, source, expected_hash):
        folder = self._folder(song_id)
        run_dir = folder / "runs" / run_id
        candidate_dir = folder / "candidates" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        try:
            if not self._heavy_slots.acquire(timeout=3600):
                raise TimeoutError("Очередь музыкальной обработки превысила час")
            try:
                with self._lock:
                    if run_id in self._cancelled:
                        return
                    m = self._manifest(song_id)
                    if m.get("active_job", {}).get("run_id") != run_id:
                        return
                    m["active_job"].update(state="separating", stage="separating", progress=None)
                    m["revision"] += 1
                    self._write(folder / "manifest.json", m)
                configured = os.environ.get("DEWIAR_SEPARATION_PYTHON")
                candidates = ([Path(configured)] if configured else [])
                candidates += [Path.home() / "Library/Application Support/Dewiar/runtimes/separation/bin/python"]
                if not getattr(sys, "frozen", False):
                    candidates.append(Path(__file__).resolve().parents[2] / ".songs-separation-venv/bin/python")
                py = next((candidate for candidate in candidates if candidate.is_file()), None)
                if getattr(sys, "frozen", False):
                    probe = Path(sys._MEIPASS) / "tools" / "song_separation_probe.py"
                else:
                    probe = Path(__file__).resolve().parents[2] / "tools" / "song_separation_probe.py"
                if py is None:
                    raise RuntimeError("Локальная среда разделения не найдена; установите runtime минусовок")
                if not probe.is_file():
                    raise RuntimeError("Worker минусовок не включён в сборку приложения")
                env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
                env.pop("PYTHONHOME", None); env.pop("PYTHONPATH", None); env.pop("DYLD_LIBRARY_PATH", None)
                worker_source = source
                # The external HTDemucs environment's libsndfile cannot decode
                # WebM/Opus (used by YouTube captures). Decode a temporary PCM
                # copy with the app's bundled ffmpeg; keep hashing the original.
                if source.suffix.lower() in {".webm", ".opus", ".m4a", ".mp3", ".aac", ".mp4"}:
                    import imageio_ffmpeg
                    worker_source = run_dir / "input.wav"
                    decoded = subprocess.run(
                        [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostdin", "-y",
                         "-protocol_whitelist", "file,pipe", "-i", str(source), "-vn",
                         "-c:a", "pcm_f32le", str(worker_source)],
                        capture_output=True, timeout=180,
                    )
                    if decoded.returncode or not worker_source.is_file() or not worker_source.stat().st_size:
                        raise RuntimeError("Не удалось декодировать аудио для создания минусовки")
                with (run_dir / "worker.log").open("w", encoding="utf-8") as log:
                    proc = subprocess.Popen([str(py), "-B", str(probe), str(worker_source), str(candidate_dir)],
                                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                            text=True, env=env)
                    with self._lock:
                        self._processes[run_id] = proc
                    while proc.poll() is None:
                        if run_id in self._cancelled:
                            proc.terminate()
                            try: proc.wait(timeout=5)
                            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
                            return
                        time.sleep(.25)
                if run_id in self._cancelled:
                    return
                if proc.returncode:
                    raise RuntimeError("HTDemucs завершился с ошибкой; см. журнал задачи")
                result = json.loads((candidate_dir / "separation.json").read_text(encoding="utf-8"))
                digest = hashlib.sha256()
                with source.open("rb") as audio:
                    for block in iter(lambda: audio.read(1024 * 1024), b""): digest.update(block)
                if digest.hexdigest() != expected_hash:
                    raise RuntimeError("Исходная запись изменилась во время подготовки")
                inst = candidate_dir / "accompaniment.wav"
                if not inst.is_file() or inst.stat().st_size == 0:
                    raise RuntimeError("Разделитель не создал минусовку")
                entry = {"candidate_id": run_id, "source_sha256": expected_hash,
                         "model": result.get("model"), "runtime": {"torch": "2.11.0", "demucs": "4.0.1"},
                         "sample_rate": result.get("sample_rate"), "samples": result.get("samples"),
                         "artifact": str(inst.relative_to(folder)), "quality_status": "unreviewed",
                         "technical_status": "ready", "seconds": result.get("seconds"),
                         "created_at": time.time()}
                with self._lock:
                    m = self._manifest(song_id)
                    if run_id in self._cancelled or m.get("active_job", {}).get("run_id") != run_id:
                        return
                    m.setdefault("candidates", []).append(entry)
                    m["selected_candidate"] = run_id
                    m["active_job"].update(state="ready", stage="ready", progress=100)
                    m["revision"] += 1
                    self._write(folder / "manifest.json", m)
            finally:
                self._heavy_slots.release()
        except Exception as exc:
            with self._lock:
                m = self._manifest(song_id)
                if m.get("active_job", {}).get("run_id") == run_id:
                    m["active_job"].update(state="cancelled" if run_id in self._cancelled else "failed",
                                            stage="done", error=str(exc)[:500])
                    m["revision"] += 1
                    self._write(folder / "manifest.json", m)
        finally:
            with self._lock:
                self._processes.pop(run_id, None)
                self.emit("song_instrumental", song_id=song_id, summary=self.summary(song_id))

    def path_for(self, song_id):
        m = self._manifest(song_id)
        candidate = next((c for c in m.get("candidates", []) if c.get("candidate_id") == m.get("selected_candidate")), None)
        if not candidate:
            return None
        path = (self._folder(song_id) / candidate["artifact"]).resolve()
        if self._folder(song_id) not in path.parents or not path.is_file():
            return None
        return path
