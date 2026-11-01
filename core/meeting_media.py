"""Проигрыватель прошлой встречи в студии: звук по дорожкам, кадры и расшифровка по одной шкале времени.

Шкала — часы записи (позиция в WAV): по ним сохраняются время реплик (номер отсчёта),
кадры (``audio_seconds`` в frames.jsonl и в sidecar .json) и видео. Поэтому кадр и строку
расшифровки можно показать ровно в момент, когда они звучали, даже если видео не собрано.

Большой WAV (115 МБ на час) не гоняется через WebSocket: его отдаёт маленький локальный
HTTP-сервер с поддержкой Range (перемотка в <audio>). Сервер слушает только 127.0.0.1,
адрес содержит случайный ключ процесса, отдаёт только файлы внутри «Встречи» с медиа-расширениями.
"""
import json
import mimetypes
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

MEDIA_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4",
               ".mov": "video/quicktime", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp"}
FRAME_EXTS = (".jpg", ".jpeg", ".png", ".webp")
LINE_RX = re.compile(r"^\[(\d+):(\d\d)\]\s*([^:]+):\s*(.*)$")
WHO = {"Вы": "me", "Собеседник": "them", "Экран": "screen"}


class MediaServer:
    """Локальная раздача файлов встреч: http://127.0.0.1:<порт>/<ключ>/<путь внутри «Встречи»>."""

    def __init__(self, meetings_root):
        self._root_fn = meetings_root if callable(meetings_root) else (lambda: meetings_root)
        self.key = secrets.token_urlsafe(18)
        self._srv = None
        self._lock = threading.Lock()

    @property
    def root(self):
        return Path(self._root_fn()).resolve()

    def resolve(self, rel):
        """Путь внутри «Встречи» → файл; всё остальное — None."""
        rel = str(rel or "").replace("\\", "/").lstrip("/")
        if rel.startswith("Встречи/"):
            rel = rel[len("Встречи/"):]
        if not rel or ".." in Path(rel).parts:
            return None
        root = self.root
        p = (root / rel).resolve()
        if root not in p.parents or not p.is_file() or p.suffix.lower() not in MEDIA_TYPES:
            return None
        return p

    def url(self, rel):
        """Адрес файла встречи для <audio>/<img>; сервер поднимается при первом обращении."""
        rel = str(rel or "").replace("\\", "/")
        if rel.startswith("Встречи/"):
            rel = rel[len("Встречи/"):]
        return f"{self.base()}{self.key}/{quote(rel, safe='/')}"

    def base(self):
        with self._lock:
            if self._srv is None:
                server = self

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *a):
                        pass

                    def do_HEAD(self):
                        self._serve(head=True)

                    def do_GET(self):
                        self._serve(head=False)

                    def _serve(self, head):
                        parts = unquote(urlsplit(self.path).path).lstrip("/").split("/", 1)
                        if len(parts) != 2 or not secrets.compare_digest(parts[0], server.key):
                            self.send_error(404)
                            return
                        p = server.resolve(parts[1])
                        if p is None:
                            self.send_error(404)
                            return
                        size = p.stat().st_size
                        start, end = 0, size - 1
                        rng = self.headers.get("Range", "")
                        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip()) if rng else None
                        if m and (m.group(1) or m.group(2)):
                            if m.group(1):
                                start = int(m.group(1))
                                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                            else:                       # bytes=-N — последние N байт
                                start = max(0, size - int(m.group(2)))
                            if start > end or start >= size:
                                self.send_response(416)
                                self.send_header("Content-Range", f"bytes */{size}")
                                self.end_headers()
                                return
                            self.send_response(206)
                            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        else:
                            self.send_response(200)
                        ctype = MEDIA_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                        self.send_header("Content-Type", ctype)
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", str(end - start + 1))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        if head:
                            return
                        try:
                            with p.open("rb") as f:
                                f.seek(start)
                                left = end - start + 1
                                while left > 0:
                                    chunk = f.read(min(256 * 1024, left))
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                                    left -= len(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            pass                        # плеер перемотал и закрыл прежний запрос

                self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                self._srv.daemon_threads = True
                threading.Thread(target=self._srv.serve_forever, name="meeting-media", daemon=True).start()
            return f"http://127.0.0.1:{self._srv.server_address[1]}/"

    def close(self):
        with self._lock:
            if self._srv is not None:
                self._srv.shutdown()
                self._srv.server_close()
                self._srv = None


def _wav_seconds(path):
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except (OSError, EOFError, wave.Error):
        return 0.0


def _read_lines(text_dir, stem):
    """Реплики встречи: сырой .lines.json (точные секунды), иначе разбор .txt «[мм:сс] Кто: текст»."""
    raw = text_dir / f"{stem}.lines.json"
    try:
        data = json.loads(raw.read_text(encoding="utf-8"))
        rows = data.get("lines") if isinstance(data, dict) else data
        out = [{"who": str(x.get("who") or ""), "text": str(x.get("text") or ""), "t": float(x.get("t") or 0)}
               for x in rows or [] if isinstance(x, dict) and x.get("who") != "screen" and str(x.get("text") or "").strip()]
        if out:
            return sorted(out, key=lambda x: x["t"])
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    out = []
    try:
        for ln in (text_dir / f"{stem}.txt").read_text(encoding="utf-8").splitlines():
            m = LINE_RX.match(ln.strip())
            if m and WHO.get(m.group(3).strip()) != "screen":
                out.append({"who": WHO.get(m.group(3).strip(), "them"), "text": m.group(4).strip(),
                            "t": int(m.group(1)) * 60 + int(m.group(2))})
    except OSError:
        pass
    return out


def _read_frames(text_dir, session):
    """Кадры по шкале записи: события frames.jsonl (с возвратами к прошлым слайдам) или время из sidecar .json."""
    out = []
    events = session / "frames.jsonl"
    if events.is_file():
        for ln in events.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            p = (text_dir / str(ev.get("path") or "")).resolve()
            if p.is_file() and p.suffix.lower() in FRAME_EXTS:
                t = ev.get("audio_seconds", ev.get("elapsed_seconds", 0))
                out.append({"file": p, "t": float(t or 0)})
    if not out and (session / "Кадры").is_dir():
        for p in sorted((session / "Кадры").iterdir()):
            if p.suffix.lower() not in FRAME_EXTS:
                continue
            t = 0.0
            try:
                stamp = json.loads(p.with_suffix(p.suffix + ".json").read_text(encoding="utf-8"))
                t = float(stamp.get("audio_seconds", stamp.get("elapsed_seconds", 0)) or 0)
            except (OSError, ValueError, TypeError):
                pass
            out.append({"file": p, "t": t})
    out.sort(key=lambda x: x["t"])
    return out


def player_data(meetings_root, summary_rel, media):
    """Всё для проигрывателя одной встречи: дорожки, видео, кадры и реплики с секундами шкалы записи."""
    root = Path(meetings_root).resolve()
    rel = str(summary_rel or "").replace("\\", "/")
    name = rel[len("Встречи/"):] if rel.startswith("Встречи/") else rel
    if "/" in name or not name.endswith(".md"):
        raise ValueError("это не итог встречи")
    md = root / name
    if not md.is_file():
        raise ValueError("итог встречи не найден")
    stem = md.stem
    audio_dir, text_dir = root / "Аудио", root / "Расшифровки"
    session = text_dir / stem
    to_rel = lambda p: p.resolve().relative_to(root).as_posix()

    tracks = []
    mix = audio_dir / f"{stem}.wav"
    if mix.is_file() and mix.stat().st_size > 44:
        tracks.append({"key": "mix", "label": "Общая", "url": media.url(to_rel(mix)), "seconds": _wav_seconds(mix)})
    for key, label, fname in (("me", "Вы", "Вы.wav"), ("them", "Собеседник", "Собеседник.wav")):
        p = audio_dir / stem / fname
        if p.is_file() and p.stat().st_size > 44:
            tracks.append({"key": key, "label": label, "url": media.url(to_rel(p)), "seconds": _wav_seconds(p)})

    video = ""
    if (session / "Видео").is_dir():
        mp4 = sorted(x for x in (session / "Видео").glob("*.mp4") if x.is_file())
        video = media.url(to_rel(mp4[0])) if mp4 else ""

    frames = [{"url": media.url(to_rel(x["file"])), "t": round(x["t"], 2), "name": x["file"].name}
              for x in _read_frames(text_dir, session)]
    lines = _read_lines(text_dir, stem)
    duration = max([x["seconds"] for x in tracks] + [x["t"] for x in frames] + [x["t"] for x in lines] + [0.0])
    return {"name": stem, "tracks": tracks, "video": video, "frames": frames, "lines": lines,
            "duration": round(duration, 2)}
