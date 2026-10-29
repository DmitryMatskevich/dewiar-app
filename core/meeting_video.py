"""Local meeting-frame timestamps and a frame/audio MP4 export."""
import json
import os
import subprocess
import tempfile
import wave
from datetime import datetime
from pathlib import Path


def frame_stamp(timestamp, elapsed, audio_time):
    dt = datetime.fromtimestamp(timestamp).astimezone()
    return {"captured_at": dt.isoformat(timespec="milliseconds"),
            "captured_at_unix": timestamp, "elapsed_seconds": round(elapsed, 3),
            "audio_seconds": round(audio_time, 3)}


def write_frame(path, data, stamp, quality=None):
    """Embed the capture time, retaining a sidecar even for unsupported input."""
    from io import BytesIO
    from PIL import Image, PngImagePlugin
    path = Path(path)
    try:
        image = Image.open(BytesIO(data))
        exif = image.getexif()
        dt = datetime.fromisoformat(stamp["captured_at"])
        exif[306] = dt.strftime("%Y:%m:%d %H:%M:%S")
        details = exif.get_ifd(34665)
        details[36867] = details[36868] = dt.strftime("%Y:%m:%d %H:%M:%S")
        details[37521] = f"{dt.microsecond:06d}"
        zone = dt.strftime("%z")
        details[36881] = zone[:3] + ":" + zone[3:]
        exif[34665] = details
        exif[270] = stamp["captured_at"]
        if path.suffix.lower() in (".jpg", ".jpeg"):
            image.convert("RGB").save(path, "JPEG", quality=quality or 85, optimize=True, exif=exif)
        else:
            info = PngImagePlugin.PngInfo()
            info.add_text("Creation Time", stamp["captured_at"])
            info.add_text("CapturedAt", stamp["captured_at"])
            image.save(path, "PNG", pnginfo=info, exif=exif)
    except (OSError, ValueError):
        path.write_bytes(data)
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(stamp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.utime(path, (stamp["captured_at_unix"], stamp["captured_at_unix"]))


def audio_duration(path):
    with wave.open(str(path), "rb") as recording:
        return recording.getnframes() / recording.getframerate()


def build_meeting_video(events, audio_path, root, output, ffmpeg):
    """Use recorded-audio offsets, including pauses, rather than capture cadence."""
    root, output = Path(root), Path(output)
    if not Path(audio_path).is_file():
        raise ValueError("Нет сохранённой аудиозаписи встречи.")
    duration = audio_duration(audio_path)
    if duration <= 0:
        raise ValueError("Аудиозапись встречи пуста.")
    timeline = {}
    for event in events:
        offset = max(0.0, float(event["audio_seconds"]))
        path = (root / event["path"]).resolve()
        if not path.is_file():
            raise ValueError("Не найден сохранённый кадр: " + path.name)
        if offset <= duration:
            # A capture made at the last audio sample occupies the final video frame.
            timeline[min(offset, max(0.0, duration - 1 / 25))] = path
    frames = sorted(timeline.items())
    if not frames:
        raise ValueError("Нет сохранённых кадров для видео встречи.")
    if not ffmpeg:
        raise ValueError("Для создания видео требуется ffmpeg.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meeting-video-", dir=output.parent) as tmp:
        tmp = Path(tmp)
        if frames[0][0] > 0:
            from PIL import Image
            blank = tmp / "before-first-frame.png"
            Image.new("RGB", (1280, 720), "black").save(blank)
            frames.insert(0, (0.0, blank))
        manifest = tmp / "frames.ffconcat"
        converted = {}
        def file_line(path):
            # ffconcat needs a single image codec and fixed geometry, even
            # when manual PNGs and automatic JPEGs are mixed.
            if path not in converted:
                from PIL import Image, ImageOps
                picture = Image.open(path).convert("RGB")
                picture = ImageOps.contain(picture, (1280, 720))
                canvas = Image.new("RGB", (1280, 720), "black")
                canvas.paste(picture, ((1280 - picture.width) // 2, (720 - picture.height) // 2))
                normalized = tmp / f"scene-{len(converted):06d}.png"
                canvas.save(normalized)
                converted[path] = normalized
            return "file '" + str(converted[path]).replace("'", "'\\''") + "'\n"
        text = "ffconcat version 1.0\n"
        for index, (offset, path) in enumerate(frames):
            end = frames[index + 1][0] if index + 1 < len(frames) else duration
            text += file_line(path) + f"duration {end - offset:.6f}\n"
        text += file_line(frames[-1][1])
        manifest.write_text(text, encoding="utf-8")
        pending = tmp / "meeting.mp4"
        command = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
                   "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
                   "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "128k", "-t", str(duration), "-movflags", "+faststart",
                   str(pending)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=max(120, duration * 3))
        if result.returncode or not pending.is_file():
            raise RuntimeError("Не удалось собрать видео встречи: " + result.stderr[-1200:])
        os.replace(pending, output)
    return str(output)
