"""Sparse video-frame OCR for speaker contact cards in transcription batches."""
import json
import copy
import re
import shutil
import subprocess
import tempfile
from pathlib import Path



CONTACT_HINTS = re.compile(r"почт|e[ -]?mail|электронн|телефон|сайт|связаться|контакт|напишите|звоните|\bmail\b|\bтел\b", re.I)
VERSION = 5


def _stamp(value):
    return f"{max(0, float(value)):.3f}"


def _candidate_groups(segments, attribution, duration=0):
    """Sparse per-speaker windows: one minute before onset through five minutes after."""
    turns = sorted((attribution or {}).get("turns") or [], key=lambda x: float(x.get("start") or 0))
    speakers = (attribution or {}).get("speakers") or []
    names = {x.get("id"): x.get("name") or x.get("label") or x.get("display") or "" for x in speakers}
    first_turns = {}
    for turn in turns:
        sid = turn.get("speaker")
        if sid:
            first_turns.setdefault(sid, turn)
    groups = []
    for sid, first in sorted(first_turns.items(), key=lambda pair: float(pair[1].get("start") or 0)):
        onset = max(0.0, float(first.get("start") or 0))
        before = max(0.0, onset - 60.0)
        after = min(float(duration or onset + 300.0), onset + 300.0)
        times = {round(before, 1): "минуту до выступления", round(max(0.0, onset - 0.5), 1): "представление выступающего",
                 round(onset + 0.5, 1): "начало выступления"}
        tick = before + 30.0
        while tick < after:
            times.setdefault(round(tick, 1), "в первые пять минут выступления")
            tick += 30.0
        # The ASR first line can lag behind the actual voice onset while the speaker
        # checks the connection or waits for the presentation to load.
        first_line = next((x for x in segments if len(x) >= 3 and onset <= float(x[0]) <= onset + 20), None)
        if first_line:
            times.setdefault(round(float(first_line[0]) + 0.25, 1), "начало доклада")
        # Transcript cues provide sparse additional checks later in the talk.
        for item in segments:
            if len(item) < 3:
                continue
            start, end, text = float(item[0]), float(item[1]), str(item[2])
            if not (CONTACT_HINTS.search(text) or re.search(
                    r"(?i)(?:https?://|www\.|[a-z0-9._%+-]+\s*@|[a-z0-9-]+\.(?:ru|com|net|org|io|рф)\b)", text)):
                continue
            if start < before or start > after:
                continue
            midpoint = (start + end) / 2
            turn = next((t for t in turns if t.get("speaker") == sid and float(t.get("start") or 0) <= midpoint <= float(t.get("end") or 0)), None)
            if turn:
                times.setdefault(round(start + min(1.5, max(0.0, end - start) / 2), 1), "контакты упомянуты в выступлении")
        groups.append({"speaker_id": sid, "speaker": names.get(sid, ""),
                       "candidates": sorted(times.items())})
    # Silent conference bumpers are checked as their own group. They are not
    # scanned if a contact card has already been found for that speaker.
    global_times = {round(x, 1): "заставка в начале ролика" for x in (0.8, 2.5, 5.0, 8.0) if not duration or x < duration}
    if duration:
        global_times.update({round(max(0.0, duration - x), 1): "заставка в конце ролика" for x in (10.0, 5.0, 1.0)})
    if global_times:
        groups.append({"speaker_id": "", "speaker": "", "candidates": sorted(global_times.items())})
    return groups


def _extract_frame(video_path, at, ffmpeg):
    run = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", _stamp(at),
                          "-i", str(video_path), "-frames:v", "1", "-vf", "scale='min(1600,iw)':-2",
                          "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                         capture_output=True, timeout=25)
    return run.stdout if run.returncode == 0 else b""


def _download_sparse_clip(ydl, info, at, index, ffmpeg, directory):
    """Fetch one short range from an already-resolved source, then return its temporary clip."""
    ydl.params["download_ranges"] = lambda _info, _ydl: [{"start_time": max(0.0, at - 0.8),
                                                           "end_time": at + 1.2, "index": index}]
    ydl.params["outtmpl"]["default"] = str(directory / f"contact-{index:04d}.%(ext)s")
    ydl.params["ffmpeg_location"] = ffmpeg
    ydl.process_ie_result(copy.deepcopy(info), download=True)
    return next((p for p in directory.glob(f"contact-{index:04d}.*") if p.is_file()), None)


from contact_extraction import has_contacts


def _report_markdown(topic, rows):
    lines = ["# Участники и контакты", "", f"**Тема:** {topic or 'Тема не указана'}", "",
             "Тему можно отредактировать прямо в этом файле.", ""]
    people = {}
    for row in rows:
        name = re.sub(r"\s*\([^)]*\)", "", row.get("speaker") or "").strip() or "Контактное лицо"
        person = people.setdefault(name, {"email": [], "phone": [], "website": [], "frames": []})
        for kind in ("email", "phone", "website"):
            for value in (row.get("contacts") or {}).get(kind, []):
                clean = re.sub(r"(?i)^www\.", "", value) if kind == "website" else value
                if clean not in person[kind]:
                    person[kind].append(clean)
        if row.get("frame"):
            person["frames"].append(row["frame"])
    for name, person in people.items():
        lines += [f"## {name}", ""]
        for label, key in (("Почта", "email"), ("Телефон", "phone"), ("Сайт", "website")):
            if person[key]:
                lines.append(f"**{label}:** {', '.join(person[key])}")
        if person["frames"]:
            lines += ["", "Кадры:"]
            for index, frame in enumerate(dict.fromkeys(person["frames"]), 1):
                lines.append(f"- [Кадр с контактами {index}]({frame})")
        lines.append("")
    if not people:
        lines += ["Контактные данные не найдены.", ""]
    return "\n".join(lines)


def extract(source, directory, audio_cache, segments, cancel=None, notify=lambda _m: None):
    """Scan each speaker's bounded lead-in and first five minutes, stopping on that speaker's first contact card."""
    directory = Path(directory)
    cache = Path(audio_cache)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    existing_json = directory / "participants-contacts.json"
    if existing_json.is_file() and (directory / "participants-contacts.md").is_file():
        try:
            existing = json.loads(existing_json.read_text(encoding="utf-8"))
            if existing.get("version") == VERSION and existing.get("source") == source:
                return directory / "participants-contacts.md"
        except (OSError, ValueError):
            pass
    media = cache / "audio-cache.json"
    cache_info = {}
    try:
        cached = json.loads(media.read_text(encoding="utf-8"))
        cache_info = cached.get("info") or {}
        video_path = cache / cached["file"]
    except (OSError, ValueError, KeyError):
        video_path = None
    if not video_path or not video_path.is_file():
        video_path = Path(source).expanduser() if not str(source).startswith(("http://", "https://")) else None
    ffmpeg = None
    try:
        from eyes import ffmpeg_path
        ffmpeg = ffmpeg_path()
    except Exception:
        pass
    if not ffmpeg:
        raise RuntimeError("ffmpeg недоступен для выборочного извлечения кадров")
    duration = float((metadata.get("summary_info") or {}).get("duration") or metadata.get("duration") or cache_info.get("duration") or 0)
    if not duration and video_path and video_path.is_file():
        probe = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(video_path), "-f", "null", "-"],
                               capture_output=True, timeout=35)
        info = (probe.stderr or b"").decode("utf-8", errors="replace")
        duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
        duration = (int(duration_match.group(1)) * 3600 + int(duration_match.group(2)) * 60 +
                    float(duration_match.group(3))) if duration_match else 0
    attribution_path = directory / "speaker-attribution.json"
    attribution = json.loads(attribution_path.read_text(encoding="utf-8")) if attribution_path.is_file() else {}
    groups = _candidate_groups(segments, attribution, duration)
    notify("Проверяю выступления на наличие контактных данных.")
    if video_path and video_path.is_file():
        check = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(video_path), "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"],
                               capture_output=True, timeout=20)
        has_video = check.returncode == 0
    else:
        has_video = False
    sparse_temp = tempfile.TemporaryDirectory(prefix="dewiar-contact-sections-")
    ydl = info = None
    if not has_video and str(source).startswith(("http://", "https://")):
        try:
            import yt_dlp
            opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                    "format": "best[height<=480]/best",
                    "outtmpl": {"default": str(Path(sparse_temp.name) / "contact.%(ext)s")},
                    "force_keyframes_at_cuts": True, "ffmpeg_location": ffmpeg,
                    "socket_timeout": 25, "retries": 2, "extractor_retries": 2}
            ydl = yt_dlp.YoutubeDL(opts)
            info = ydl.extract_info(source, download=False)
        except Exception as exc:
            sparse_temp.cleanup()
            raise RuntimeError(f"Не удалось подготовить выборочное видео: {exc}") from exc
    stage_frames = directory / ".contact-frames-new"
    if stage_frames.exists():
        shutil.rmtree(stage_frames)
    stage_frames.mkdir()
    rows, frames_checked, index = [], 0, 0
    speakers = attribution.get("speakers") or []
    by_id = {x.get("id"): x for x in speakers}
    try:
        for group in groups:
            for at, reason in group["candidates"]:
                if cancel is not None and cancel.is_set():
                    break
                index += 1
                if has_video:
                    frame = _extract_frame(video_path, at, ffmpeg)
                elif ydl and info:
                    clip = _download_sparse_clip(ydl, info, at, index, ffmpeg, Path(sparse_temp.name))
                    frame = _extract_frame(clip, 0.8, ffmpeg) if clip else b""
                else:
                    frame = b""
                if not frame:
                    continue
                frames_checked += 1
                from image_contacts import extract_bytes as extract_image_contacts
                result = extract_image_contacts(frame)
                contacts = result.get("contacts") or {}
                if not has_contacts(contacts):
                    continue
                frame_name = f"contact-{len(rows)+1:02d}-{int(at*10):07d}.png"
                (stage_frames / frame_name).write_bytes(frame)
                person = by_id.get(group.get("speaker_id"), {})
                rows.append({"time": round(at, 2), "reason": reason,
                             "speaker": person.get("name") or person.get("label") or group.get("speaker") or "",
                             "contacts": contacts, "frame": f"contact-frames/{frame_name}"})
                label = rows[-1]["speaker"] or "Контактное лицо"
                notify(f"Контакты найдены: {label}.")
                break
            if cancel is not None and cancel.is_set():
                break
    finally:
        if ydl:
            ydl.close()
        sparse_temp.cleanup()
    frames_dir = directory / "contact-frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    if rows:
        stage_frames.rename(frames_dir)
    else:
        shutil.rmtree(stage_frames)
    title = metadata.get("title") or Path(str(source).split("?")[0]).stem
    topic = title
    (directory / "participants-contacts.md").write_text(_report_markdown(topic, rows), encoding="utf-8")
    (directory / "participants-contacts.json").write_text(json.dumps({"version": VERSION, "source": source, "topic": topic, "frames_checked": frames_checked,
        "contacts_found": len(rows), "participants": speakers, "items": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return directory / "participants-contacts.md"
