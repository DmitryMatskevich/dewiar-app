"""Команды проверки текста, общие для ядра и изолированного UI стенда.

Модели и настройки приложения не импортируются. Для прослушивания отдаётся
ограниченный фрагмент локальной записи; ffmpeg не получает сетевые протоколы.
"""
import base64
import shutil
import subprocess


def review_command(store, cmd, args):
    song_id = str(args.get("song_id") or "")
    if cmd == 'song_finish_review':
        if args.get('lyrics_checked') is not True or args.get('chords_checked') is not True:
            raise ValueError('Подтвердите проверку текста и аккордов по записи')
        if args.get('base_revision') is None:
            raise ValueError('Для сохранения нужна ревизия карточки')
        with store._lock:
            row = store.get(song_id)
            if not row or row.get('status') in {'running','recording'}:
                raise ValueError('Сначала завершите обработку записи')
            if not row.get('phrases') or not any(c.get('name') != 'N' for c in row.get('chords',[])):
                raise ValueError('Сначала получите текст и подбор аккордов')
            phrases = [{**p,'review_state':'verified'} for p in row['phrases']]
            chords = [{**c,'verified':True} for c in row['chords']]
            history = row.get('phrase_history',{})
            history.setdefault('_review',[]).append({'previous_review_state':row.get('review_state'),
                                                    'revision':row['revision']})
            from .chord_layout import build_guitar_sheet
            return store.patch(song_id,{'status':'ready','review_state':'verified',
                'stage_label':'Текст и аккорды проверены пользователем','phrases':phrases,'chords':chords,
                'guitar_sheet':build_guitar_sheet(phrases,chords),'phrase_history':history},args['base_revision'])
    if cmd == 'song_edit_structure':
        from .arrangement import edit_structure
        return edit_structure(store, args)
    if cmd == 'song_edit_chords':
        from .arrangement import edit_chords
        return edit_chords(store, args)
    if cmd == "song_edit_phrase":
        return store.edit_phrase(song_id, str(args.get("phrase_id") or ""), args.get("text"),
                                 args.get("note", ""), args.get("reviewed") is True,
                                 args.get("base_revision"), args.get("action", "edit"))
    if cmd not in {"song_phrase_audio", "song_full_audio"}:
        raise ValueError("Неизвестная команда проверки")
    row = store.get(song_id)
    if not row:
        raise ValueError("Песня не найдена")
    full = cmd == 'song_full_audio'
    phrase = {'start_ms':0,'end_ms':row.get('audio',{}).get('duration_ms') or 600_000} if full else next((p for p in row.get("phrases", []) if p.get("phrase_id") == args.get("phrase_id")), None)
    if phrase is None:
        raise ValueError("Фраза не найдена")
    root = store.root.resolve()
    path = (root / row.get("audio", {}).get("path", "")).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("Запись недоступна — нужна повторная запись")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            pass
    if not ffmpeg:
        raise ValueError("Для прослушивания записи нужен ffmpeg")
    start = max(0, int(phrase["start_ms"]) - 350)
    end = int(phrase["end_ms"]) + (0 if full else 350)
    if end <= start or end - start > (600_000 if full else 120_000):
        raise ValueError("Для длинной записи используйте прослушивание по фразам")
    # Ten minutes of PCM exceed the bridge's 16 MiB JSON frame limit.
    # CBR MP3 at 128 kbit/s keeps full playback below 13 MiB after base64.
    encoding = (["-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]
                if full else ["-ac", "1", "-ar", "16000", "-f", "wav"])
    try:
        result = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
            "-ss", str(start / 1000), "-i", str(path), "-t", str((end - start) / 1000),
            "-vn", *encoding, "pipe:1"], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Не удалось подготовить фрагмент за 30 секунд") from exc
    if result.returncode or len(result.stdout) < 80:
        raise ValueError("Не удалось прочитать аудиозапись")
    if len(result.stdout) > 11_500_000:
        raise ValueError("Запись слишком велика для плеера; используйте прослушивание по фразам")
    mime = 'audio/mpeg' if full else 'audio/wav'
    return {"data": f"data:{mime};base64," + base64.b64encode(result.stdout).decode(),
            "start_ms": start, "end_ms": end, "phrase_start_ms": phrase["start_ms"], "phrase_end_ms": phrase["end_ms"]}
