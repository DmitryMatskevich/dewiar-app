"""Real MP4/audio and capture metadata, using generated fixtures only."""
import io
from datetime import datetime, timezone
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from PIL import Image
from meeting_video import build_meeting_video, frame_stamp, write_frame
from eyes import ffmpeg_path
from prompter import Prompter

root = Path(tempfile.mkdtemp(prefix="meeting-video-test-"))
try:
    stamp = frame_stamp(1793288605.123, 12.25, 1.0)
    paths = []
    for color, name in [("red", "red.png"), ("blue", "blue.jpg")]:
        buf = io.BytesIO()
        Image.new("RGB", (160, 90), color).save(buf, "PNG")
        path = root / name
        write_frame(path, buf.getvalue(), stamp, 80)
        im = Image.open(path)
        details = im.getexif().get_ifd(34665)
        exif_time = datetime.strptime(details[36867] + details[36881], "%Y:%m:%d %H:%M:%S%z")
        assert exif_time.astimezone(timezone.utc).isoformat() == "2026-10-29T15:43:25+00:00"
        assert im.getexif().get_ifd(34665)[37521].startswith("123")
        assert json.loads(path.with_suffix(path.suffix + ".json").read_text())["audio_seconds"] == 1
        paths.append(path)
    audio = root / "voice.wav"
    with wave.open(str(audio), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        # Synthetic audible tone, not microphone or personal data.
        import math, struct
        out.writeframes(b"".join(struct.pack("<h", int(1000 * math.sin(n * 2 * math.pi * 440 / 16000))) for n in range(48000)))
    events = [{"path": paths[0].name, "audio_seconds": 0},
              {"path": paths[1].name, "audio_seconds": 1},
              {"path": paths[0].name, "audio_seconds": 2}]
    output = root / "video with ' quote.mp4"
    ff = ffmpeg_path()
    assert ff, "ffmpeg required for this integration test"
    build_meeting_video(events, audio, root, output, ff)
    raw = output.read_bytes()
    assert raw.index(b"moov") < raw.index(b"mdat"), "Not faststart"
    info = subprocess.run([ff, "-i", str(output)], text=True, capture_output=True).stderr
    assert "Audio: aac" in info and "Video: h264" in info, info
    for offset, red in [(0.3, True), (1.3, False), (2.3, True)]:
        result = subprocess.run([ff, "-ss", str(offset), "-i", str(output), "-frames:v", "1",
                                 "-f", "image2pipe", "-vcodec", "png", "-"],
                                capture_output=True, check=True)
        pixel = Image.open(io.BytesIO(result.stdout)).getpixel((640, 360))
        assert (pixel[0] > pixel[2]) == red, (offset, pixel)
    # Validate the backend's audio clock independently from elapsed wall time.
    engine = SimpleNamespace(workspace=SimpleNamespace(ensure=lambda: root),
        s=SimpleNamespace(active_llm=lambda: None),
        emit=lambda *a, **k: None, set_state=lambda *a: None)
    p = Prompter(engine)
    p.on = True
    p.meeting = {"record": True, "started": time.time() - 100, "title": "Fixture"}
    p._transcript_open("Fixture")
    p._record_open("Fixture")
    p._record_write(b"\0\0" * 16000)
    data = io.BytesIO()
    Image.new("RGB", (160, 90), "red").save(data, "PNG")
    entry = p.capture_frame(data.getvalue(), ocr=False)
    assert entry["elapsed_seconds"] >= 100
    assert entry["audio_seconds"] == 1, entry
    p._record_close()
    p.on = False
    p.export_meeting_video()
    deadline = time.monotonic() + 15
    while p._video_export_busy and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not p._video_export_busy and Path(p._video_path).is_file()
    assert (p._session_dir / "frames.jsonl").is_file()
    print("OK: EXIF/PNG timestamps, JSON sidecars, audio-clock timeline, red/blue/red video, audio, faststart, async export")
finally:
    shutil.rmtree(root, ignore_errors=True)
