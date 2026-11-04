"""Bounded local microphone or system-audio capture for the Songs tab."""
from __future__ import annotations

import threading
import time
import wave
import uuid
from pathlib import Path

from audio_io import Capture, LoopbackCapture


class SongCaptureManager:
    sample_width = 2

    def __init__(self, root: Path, store, emit):
        self.root = Path(root)
        self.store = store
        self.emit = emit
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}

    def state(self, song_id: str = ""):
        def public(item):
            return {"song_id": item["song_id"], "running": not item["stop"].is_set(),
                    "duration_ms": int(item["frames"] * 1000 / item["rate"]),
                    "source": str(item["source"]), "error": item["error"]}
        with self._lock:
            if song_id:
                item = self._active.get(song_id)
                return public(item) if item else {"running": False, "song_id": song_id}
            return {"running": bool(self._active), "songs": [public(v) for v in self._active.values()]}

    def start(self, song_id: str, target: str = "all", backend: str = "auto", source: str = "system"):
        song = self.store.get(song_id)
        if not song:
            raise ValueError("Песня не найдена")
        if song.get("phrases"):
            raise ValueError("Для нового исполнения создайте новую карточку — прежние правки относятся к этой записи")
        with self._lock:
            if self._active:
                raise ValueError("Сначала завершите текущую запись песни")
            folder = self.root / song_id / "audio"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"capture-{uuid.uuid4().hex}.wav"
            source_mode = str(source or "system").lower()
            if source_mode == "microphone":
                # Direct input capture records a live singer; system capture below is
                # for songs already playing on this Mac (Yandex Music, browser, etc.).
                cap = Capture(device="", channels=1)
            elif source_mode == "system":
                cap = LoopbackCapture(backend=str(backend or "auto"), tap_target=str(target or "all"))
            else:
                raise ValueError("Неизвестный источник записи песни")
            try:
                source = cap.open()
            except Exception:
                cap.close()
                raise
            native = getattr(cap, "_cap", None) if getattr(cap, "kind", "") == "tap" else None
            rate = int(getattr(native, "rate", 16000) or 16000)
            channels = int(getattr(native, "channels", getattr(cap, "channels", 1)) or 1)
            handle = {"song_id": song_id, "path": path, "capture": cap, "native": native, "stop": threading.Event(),
                      "thread": None, "source": source, "microphone": source_mode == "microphone",
                      "started": time.time(), "frames": 0, "error": ""}
            handle["rate"], handle["channels"] = rate, channels
            self._active[song_id] = handle
            self.store.patch(song_id, {"status": "recording", "analysis_state": "capturing", "stage": "record",
                                       "stage_label": "Запись с микрофона; голосовой ввод выключен" if source_mode == "microphone" else "Записываем звук компьютера",
                                       "audio": {"path": str(path.relative_to(self.root)),
                                       "format": "wav", "sample_rate": rate, "channels": channels,
                                       "owned": True, "duration_ms": 0, "capture_source": source_mode},
                                       "capabilities": {**(song.get("capabilities") or {}), "audio": False}})
            thread = threading.Thread(target=self._run, args=(handle,), name=f"song-capture-{song_id[:8]}", daemon=True)
            handle["thread"] = thread
            thread.start()
            self.emit("song_capture", song_id=song_id, running=True, source=source)
            return self.state(song_id)

    def stop(self, song_id: str):
        with self._lock:
            handle = self._active.get(song_id)
            if not handle:
                raise ValueError("Запись песни не запущена")
            handle["stop"].set()
            thread = handle.get("thread")
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                handle["capture"].close()
                thread.join(timeout=2)
            if thread.is_alive():
                raise ValueError("Источник ещё закрывается — повторите остановку через несколько секунд")
        with self._lock:
            handle = self._active.pop(song_id, handle)
        if handle.get("error"):
            self.store.patch(song_id, {"status": "failed", "stage_label": "Запись прервана: " + handle["error"][:160]})
            raise ValueError(handle["error"])
        rate = int(handle.get("rate") or 16000)
        duration_ms = int(handle.get("frames", 0) * 1000 / rate)
        song = self.store.get(song_id) or {}
        audio = dict(song.get("audio") or {})
        audio.update({"duration_ms": duration_ms})
        status = "queued" if duration_ms >= 1000 else "needs_input"
        stage_label = "Запись сохранена — можно распознавать" if status == "queued" else "Запись слишком короткая"
        updated = self.store.patch(song_id, {"status": status, "analysis_state": "captured" if status == "queued" else "capture_short",
                                             "stage": "lyrics" if status == "queued" else "record", "stage_label": stage_label,
                                             "audio": audio, "coverage": {**(song.get("coverage") or {}), "recorded_ms": duration_ms},
                                             "capabilities": {**(song.get("capabilities") or {}), "audio": status == "queued"}})
        self.emit("song_capture", song_id=song_id, running=False, duration_ms=duration_ms, song=updated)
        return updated

    def _run(self, handle):
        path = handle["path"]
        cap = handle["capture"]
        try:
            with wave.open(str(path), "wb") as out:
                out.setnchannels(int(handle.get("channels") or 1))
                out.setsampwidth(self.sample_width)
                out.setframerate(int(handle.get("rate") or 16000))
                while not handle["stop"].is_set():
                    chunk = cap.read() if handle.get("microphone") else handle["native"].read_native() if handle.get("native") else cap.read()
                    if not chunk:
                        continue
                    out.writeframesraw(chunk)
                    handle["frames"] += len(chunk) // (self.sample_width * handle["channels"])
                    if handle["frames"] % (int(handle.get("rate") or 16000) * 2) < 1024:
                        self.emit("song_capture", song_id=handle["song_id"], running=True,
                                  duration_ms=int(handle["frames"] * 1000 / int(handle.get("rate") or 16000)))
        except Exception as exc:  # noqa: BLE001 — сохраняем причину в карточке
            handle["error"] = str(exc)
        finally:
            cap.close()

    def close(self):
        for song_id in list(self._active):
            try:
                self.stop(song_id)
            except Exception:
                pass
