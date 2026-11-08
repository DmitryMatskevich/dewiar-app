"""Speaker-attributed transcript pass for batch media transcription.

Uses the same segmentation and CAM++ speaker embedding pipeline as meeting
recordings. Names are deliberately left unresolved unless the meeting profile
already identifies the owner; no names are guessed from voice clusters.
"""
import json
import subprocess
from pathlib import Path


def _time(value):
    n = max(0, int(float(value or 0)))
    return f"{n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}"


def _wav(source, work, resolve, cancel, audio_cache=None):
    from eyes import ffmpeg_path
    from media_transcription import _audio

    work.mkdir(parents=True, exist_ok=True)
    if str(source).startswith(("http://", "https://")):
        ready = None
        if audio_cache is not None:
            from media_transcription import cached_audio
            ready = cached_audio(source, audio_cache)
        media, _ = ready or _audio(source, audio_cache or work, cancel)
    else:
        media = resolve(source)
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("Для распознавания выступающих нужен ffmpeg")
    out = work / "speaker-audio.wav"
    proc = subprocess.Popen([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(media), "-vn",
                             "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    while proc.poll() is None:
        if cancel.wait(.25):
            proc.terminate()
            raise RuntimeError("Задачу остановили")
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.returncode or not out.is_file():
        raise RuntimeError("Не удалось подготовить звук для распознавания выступающих: " + err[-300:])
    return out


def attribute(source, segments, directory, resolve, cancel, prompter, notify=lambda _s: None, audio_cache=None):
    """Write speaker-labeled Markdown and JSON into the source's output folder."""
    from diarization import service

    directory = Path(directory)
    wav = _wav(source, directory / "_speaker-pass", resolve, cancel, audio_cache)
    if service.wav_seconds(wav) < service.config(prompter._prompter_cfg()).get("min_duration_sec", 20):
        raise RuntimeError("Для надёжного определения выступающих запись должна длиться не менее 20 секунд")
    txt = directory / "_speaker-pass" / "transcript.txt"
    txt.write_text("\n".join(str(x[2]) for x in segments) + "\n", encoding="utf-8")
    files = service.MeetingFiles.explicit(wav, txt)
    meeting = {"speaker_mode": "participants", "participants": [], "expected_speakers": 0}
    cfg = dict(prompter._prompter_cfg())
    # Meeting participant counts/names belong to a specific call and must not
    # leak into unrelated video URLs.
    cfg.pop("expected_speakers", None)
    cfg.pop("participants", None)
    mirror = prompter.eng._mirror("diarization_seg") if hasattr(prompter.eng, "_mirror") else None
    speaker = prompter.speaker
    if speaker.mirror is None and hasattr(prompter.eng, "_mirror"):
        speaker.mirror = prompter.eng._mirror("speaker")
    notify("Размечаю реплики по голосам алгоритмом обработки встреч…")
    job = service.start_job(files, meeting, cfg, mirror=mirror, speaker=speaker)
    result = job.wait(timeout=max(600, service.wav_seconds(wav) * 2))
    if result is None:
        raise RuntimeError(job.error or "Не удалось определить выступающих")
    lines, speakers, turns, machine = service.assemble(
        files, result, {"mix": segments}, meeting, cfg, speaker=speaker)
    names = {s.get("id"): s.get("label") or s.get("name") or f"Участник {i}"
             for i, s in enumerate(speakers, 1)}
    out_lines = ["# Расшифровка по выступающим", "",
                 "Участники определены по голосу; имена указаны только при подтверждении профилем или данными записи.", ""]
    for item in lines:
        who = item.get("label") or names.get(item.get("speaker"), "Участник")
        if item.get("who") == "me":
            who = "Вы"
        out_lines.append(f"[{_time(item.get('t'))}] **{who}:** {item.get('text', '').strip()}")
    md = directory / "transcript.speakers.md"
    md.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    payload = {"version": 1, "speakers": speakers, "speaker_clusters": machine,
               "turns": [{"start": x.start, "end": x.end, "speaker": x.speaker} for x in turns], "lines": lines}
    (directory / "speaker-attribution.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return md
