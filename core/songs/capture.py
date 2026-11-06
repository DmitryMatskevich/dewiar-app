"""Song capture owned by the core, including automatic stop and recovery."""
from __future__ import annotations

from array import array
from pathlib import Path
import queue
import struct
import sys
import threading
import time
import uuid
import wave

from audio_io import Capture, LoopbackCapture


class SongCaptureManager:
    sample_width = 2
    silence_seconds = 5.0
    no_data_seconds = 5.0
    max_seconds = 600.0
    # PCM energy, not speech VAD: instrumental passages must keep recording.
    silence_rms = 100  # approximately -50 dBFS for signed 16-bit PCM

    def __init__(self, root: Path, store, emit, on_complete=None):
        self.root, self.store, self.emit = Path(root), store, emit
        self.on_complete = on_complete
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}
        self._closing = False
        self.recover_interrupted()

    def state(self, song_id=""):
        def public(h):
            return {"song_id": h["song_id"], "running": True,
                    "closing": h["stop"].is_set(), "duration_ms": int(h["frames"] * 1000 / h["rate"]),
                    "source": str(h["source"]), "microphone": h["microphone"], "error": h["error"]}
        with self._lock:
            if song_id:
                h = self._active.get(song_id)
                return public(h) if h else {"running": False, "song_id": song_id}
            return {"running": bool(self._active), "songs": [public(h) for h in self._active.values()]}

    def start(self, song_id, target="all", backend="auto", source="system"):
        with self._lock:
            if self._active or self._closing:
                raise ValueError("Сначала завершите текущую запись песни")
            song = self.store.get(song_id)
            if not song:
                raise ValueError("Песня не найдена")
            if song.get("phrases"):
                raise ValueError("Для нового исполнения создайте новую карточку — прежние правки относятся к этой записи")
            mode = str(source or "system").lower()
            if mode not in {"microphone", "system"}:
                raise ValueError("Неизвестный источник записи песни")
            cap = Capture(device="", channels=1) if mode == "microphone" else LoopbackCapture(backend=str(backend or "auto"), tap_target=str(target or "all"))
            folder = self.root / song_id / "audio"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"capture-{uuid.uuid4().hex}.wav"
            try:
                human_source = cap.open()
                native = getattr(cap, "_cap", None) if getattr(cap, "kind", "") == "tap" else None
                rate = int(getattr(native, "rate", 16000) or 16000)
                channels = int(getattr(native, "channels", getattr(cap, "channels", 1)) or 1)
                h = {"song_id": song_id, "path": path, "capture": cap, "native": native,
                     "stop": threading.Event(), "done": threading.Event(), "closed": threading.Event(),
                     "source": human_source, "microphone": mode == "microphone", "frames": 0,
                     "rate": rate, "channels": channels, "error": "", "reason": "manual", "audible": False,
                     "queue": queue.Queue(maxsize=64)}
                self.store.patch(song_id, {"status": "recording", "analysis_state": "capturing", "stage": "record",
                    "stage_label": "Запись с микрофона · автостоп после 5 секунд тишины" if h["microphone"] else "Записываем звук компьютера",
                    "audio": {"path": str(path.relative_to(self.root)), "format": "wav", "sample_rate": rate,
                              "channels": channels, "owned": True, "duration_ms": 0, "capture_source": mode},
                    "capabilities": {**(song.get("capabilities") or {}), "audio": False}})
            except Exception:
                cap.close()
                raise
            self._active[song_id] = h
            self.emit("song_capture", song_id=song_id, running=True, source=human_source)
            h["thread"] = threading.Thread(target=self._run, args=(h,), name=f"song-capture-{song_id[:8]}", daemon=True)
            h["thread"].start()
            return self.state(song_id)

    def stop(self, song_id):
        with self._lock:
            h = self._active.get(song_id)
            if h:
                h["stop"].set()
        if h:
            if not h["done"].wait(8):
                raise ValueError("Источник ещё закрывается. Запись сохраняется; повторите остановку через несколько секунд")
        else:
            # Repeated clicks and auto-stop racing with a click are harmless.
            with self._lock:
                for saved in self.recover_interrupted(song_id):
                    if self.on_complete:
                        self.on_complete(saved, microphone=False, shutting_down=self._closing)
        saved = self.store.get(song_id)
        if not saved:
            raise ValueError("Песня не найдена")
        return saved

    def _read(self, h):
        try:
            cap = h["capture"]
            while not h["stop"].is_set():
                chunk = cap.read() if h["microphone"] else h["native"].read_native() if h["native"] else cap.read()
                if not chunk:
                    h["stop"].wait(.02)
                    continue
                while not h["stop"].is_set():
                    try:
                        h["queue"].put(bytes(chunk), timeout=.1)
                        break
                    except queue.Full:
                        pass
        except Exception as exc:
            h["error"] = str(exc)
            h["reason"] = "input_error"
            h["stop"].set()

    def _close_input(self, h):
        try:
            h["capture"].close()
        except Exception as exc:
            h["error"] = h["error"] or str(exc)
        finally:
            h["closed"].set()

    @classmethod
    def _audible(cls, chunk):
        samples = array("h", chunk)
        if sys.byteorder != "little":
            samples.byteswap()
        return bool(samples) and sum(x*x for x in samples) / len(samples) >= cls.silence_rms ** 2

    def _run(self, h):
        reader = threading.Thread(target=self._read, args=(h,), name="song-input-reader", daemon=True)
        began = last_data = last_update = time.monotonic()
        silence = 0
        frame_bytes = self.sample_width * h["channels"]
        reader.start()
        try:
            with wave.open(str(h["path"]), "wb") as out:
                out.setnchannels(h["channels"])
                out.setsampwidth(self.sample_width)
                out.setframerate(h["rate"])
                while not h["stop"].is_set() or not h["queue"].empty():
                    try:
                        chunk = h["queue"].get(timeout=.1)
                    except queue.Empty:
                        if time.monotonic() - last_data >= self.no_data_seconds:
                            h["reason"] = "no_data"
                            h["stop"].set()
                        if time.monotonic() - began >= self.max_seconds:
                            h["reason"] = "limit"
                            h["stop"].set()
                        continue
                    last_data = time.monotonic()
                    chunk = chunk[:len(chunk) // frame_bytes * frame_bytes]
                    if not chunk:
                        continue
                    # Keep a valid header after every chunk, including abrupt exits.
                    out.writeframes(chunk)
                    frames = len(chunk) // frame_bytes
                    h["frames"] += frames
                    audible = self._audible(chunk)
                    h["audible"] |= audible
                    silence = 0 if audible else silence + frames / h["rate"]
                    if h["microphone"] and silence >= self.silence_seconds:
                        h["reason"] = "silence"
                        h["stop"].set()
                        break
                    if h["frames"] / h["rate"] >= self.max_seconds or last_data - began >= self.max_seconds:
                        h["reason"] = "limit"
                        h["stop"].set()
                        break
                    if last_data - last_update >= 1:
                        last_update = last_data
                        self.emit("song_capture", song_id=h["song_id"], running=True, duration_ms=int(h["frames"] * 1000 / h["rate"]))
        except Exception as exc:
            h["error"] = str(exc)
            h["reason"] = "write_error"
        finally:
            h["stop"].set()
            threading.Thread(target=self._close_input, args=(h,), name="song-input-close", daemon=True).start()
            # The UI never waits inside an audio driver. Retain ownership until
            # close actually returns so a second capture cannot race the old one.
            if not h["closed"].wait(2):
                self.store.patch(h["song_id"], {"stage_label": "Запись завершена; ожидаем освобождения микрофона"})
            h["closed"].wait()
            reader.join(timeout=.2)
            try:
                self._finish(h)
            finally:
                h["done"].set()

    def _finish(self, h):
        song_id = h["song_id"]
        duration = int(h["frames"] * 1000 / h["rate"])
        usable = duration >= 1000 and (h["audible"] or not h["microphone"]) and h["reason"] != "write_error"
        row = self.store.get(song_id) or {}
        label = "Запись сохранена — запускаем обработку" if usable else "Звук не записан. Проверьте микрофон и начните запись снова" if not h["audible"] else "Запись слишком короткая — запишите песню снова"
        updated = self.store.patch(song_id, {"status": "queued" if usable else "needs_input",
            "analysis_state": "captured" if usable else "capture_short", "stage": "lyrics" if usable else "record",
            "stage_label": label, "audio": {**(row.get("audio") or {}), "duration_ms": duration,
                                            "path": str(h["path"].relative_to(self.root)) if usable else ""},
            "capture_result": {"reason": h["reason"], "error": h["error"], "path": str(h["path"].relative_to(self.root))},
            "coverage": {**(row.get("coverage") or {}), "recorded_ms": duration},
            "capabilities": {**(row.get("capabilities") or {}), "audio": usable}})
        with self._lock:
            # Restore the assistant input guard before allowing another capture.
            try:
                if self.on_complete:
                    self.on_complete(updated, microphone=h["microphone"], shutting_down=self._closing)
            finally:
                self._active.pop(song_id, None)
        updated = self.store.get(song_id) or updated
        self.emit("song_capture", song_id=song_id, running=False, duration_ms=duration, reason=h["reason"], song=updated)

    def recover_interrupted(self, song_id=""):
        recovered = []
        rows = [self.store.get(song_id)] if song_id else self.store.list(limit=500)
        for row in rows:
            if not row or row.get("status") not in {"recording", "interrupted"} or row.get("analysis_state") != "capturing":
                continue
            sid, audio = row["song_id"], row.get("audio") or {}
            source = (self.root / str(audio.get("path") or "")).resolve()
            folder = (self.root / sid / "audio").resolve()
            try:
                if source.parent != folder or not source.name.startswith("capture-"):
                    raise ValueError("Файл записи не найден")
                # Old raw writers left the first chunk size on crash. Recover all
                # actual PCM frames into a NEW file; preserve the original.
                with source.open("rb") as stream:
                    header = stream.read(44)
                    if len(header) != 44 or header[:4] != b"RIFF" or header[8:16] != b"WAVEfmt " or header[36:40] != b"data":
                        raise ValueError("Не удалось прочитать сохранённую запись")
                    fmt, channels, rate, _, align, bits = struct.unpack("<HHIIHH", header[20:36])
                    if fmt != 1 or bits != 16 or channels not in (1, 2) or not 8000 <= rate <= 192000 or align != channels * 2:
                        raise ValueError("Неподдерживаемый формат записи")
                    target = source.with_name(f"capture-recovered-{uuid.uuid4().hex}.wav")
                    frames, audible = 0, False
                    with wave.open(str(target), "wb") as out:
                        out.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
                        while chunk := stream.read(align * 16384):
                            chunk = chunk[:len(chunk) // align * align]
                            out.writeframesraw(chunk)
                            frames += len(chunk) // align
                            audible |= self._audible(chunk)
                duration = int(frames * 1000 / rate)
                usable = duration >= 1000 and (audible or audio.get("capture_source") != "microphone")
                saved = self.store.patch(sid, {"status": "queued" if usable else "needs_input", "analysis_state": "captured" if usable else "capture_short",
                    "stage": "lyrics" if usable else "record", "stage_label": "Запись восстановлена — продолжаем обработку" if usable else "Запись пуста или слишком короткая — запишите песню снова",
                    "audio": {**audio, "path": str(target.relative_to(self.root.resolve())) if usable else "", "duration_ms": duration, "sample_rate": rate, "channels": channels},
                    "capture_result": {"reason": "recovered", "path": str(target.relative_to(self.root.resolve()))},
                    "coverage": {**(row.get("coverage") or {}), "recorded_ms": duration},
                    "capabilities": {**(row.get("capabilities") or {}), "audio": usable}})
                recovered.append(saved)
            except (OSError, ValueError, wave.Error) as exc:
                self.store.patch(sid, {"status": "needs_input", "analysis_state": "capture_failed", "stage": "record",
                    "stage_label": f"Запись прервана: {exc}. Запишите песню снова",
                    "audio": {**audio, "path": ""}, "capabilities": {**(row.get("capabilities") or {}), "audio": False}})
        return recovered

    def close(self):
        self._closing = True
        for song_id in list(self._active):
            try:
                self.stop(song_id)
            except Exception:
                pass
