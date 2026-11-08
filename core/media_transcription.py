"""Subtitle-first transcription for the task workshop; no dependency on Codex or its skills."""
import hashlib
import json
import subprocess
import threading
import shutil
from urllib.parse import urlsplit, parse_qs, urlunsplit
from pathlib import Path

import video
from transcript_editor import ensure_clean
from transcript_summary import ensure_summary


class Cancelled(Exception):
    pass


_locks_guard = threading.Lock()
_locks = {}
FILES = ('transcript.txt', 'transcript-timed.txt', 'transcript.srt', 'segments.json', 'metadata.json', 'errors.log',
         'transcript.clean.md','transcript.review.md','cleanup.json','transcript.summary.md','transcript.topics.md','summary.json')


def source_key(source):
    value = str(source).strip()
    if value.startswith(('http://', 'https://')):
        u = urlsplit(value)
        host = (u.hostname or '').lower()
        if host == 'youtu.be' or host == 'youtube.com' or host.endswith('.youtube.com'):
            ident = u.path.strip('/') if host == 'youtu.be' else parse_qs(u.query).get('v', [u.path.rstrip('/').split('/')[-1]])[0]
            value = 'youtube:' + ident
        else:
            value = urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path, u.query, ''))
    else:
        value = str(Path(value).expanduser().resolve())
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def unique_sources(sources):
    out, seen = [], set()
    for source in sources:
        source = str(source).strip()
        if source and not source.startswith('#') and source_key(source) not in seen:
            out.append(source)
            seen.add(source_key(source))
    return out


def cached_audio(source, directory):
    try:
        info = json.loads((directory/'audio-cache.json').read_text(encoding='utf-8'))
        path = directory/info['file']
        if (info['source_key'] == source_key(source) and path.resolve().parent == directory.resolve()
                and path.is_file() and path.stat().st_size == info['bytes'] and info['bytes'] > 0):
            return path, info['info']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def seed_cache(cache, task_dir):
    """Import earlier successful app results, so the first new batch also reuses them."""
    try:
        report = json.loads((task_dir/'outputs/transcripts/batch-results.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    for row in report:
        if row.get('status') != 'ok' or not row.get('source') or not row.get('text'):
            continue
        src = (task_dir/'outputs'/row['text']).parent.resolve()
        if task_dir.resolve() not in src.parents:
            continue
        dst = cache/source_key(row['source'])
        if (dst/'metadata.json').exists() or not all((src/name).is_file() for name in FILES[:4]):
            continue
        with _locks_guard:
            lock = _locks.setdefault(str(dst.resolve()),threading.Lock())
        if not lock.acquire(blocking=False):
            continue
        try:
            if (dst/'metadata.json').exists():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                if (src/name).is_file():
                    shutil.copy2(src/name, dst/name)
        finally:
            lock.release()


def _cached_batch(sources, output, resolve, recognize, cancel, language, force, notify, pause, cache, summarize=None):
    results = []
    sources = unique_sources(sources)
    for i, source in enumerate(sources, 1):
        _check(cancel)
        key = source_key(source)
        with _locks_guard:
            lock = _locks.setdefault(str((cache/key).resolve()), threading.Lock())
        waiting = False
        while not lock.acquire(timeout=.25):
            _check(cancel)
            if not waiting:
                notify(f'{i}/{len(sources)} — этот источник уже обрабатывается, жду готовый результат')
                waiting = True
        try:
            notify(f'{i}/{len(sources)} — {source}')
            rows = run_batch([source], cache, resolve, recognize, cancel, language, force,
                             lambda message: notify(f'{i}/{len(sources)} — {message}'), 0, summarize=summarize)
            entry = dict(rows[0], source=source)
            dst = output/key
            dst.mkdir(parents=True, exist_ok=True)
            for name in FILES:
                if (cache/key/name).is_file():
                    shutil.copy2(cache/key/name, dst/name)
            if entry['status'] == 'ok' or entry.get('extraction_complete'):
                entry['text'] = (dst/'transcript.txt').relative_to(output.parent).as_posix()
                entry['srt'] = (dst/'transcript.srt').relative_to(output.parent).as_posix()
            if entry['status'] == 'ok':
                entry['markdown'] = (dst/'transcript.clean.md').relative_to(output.parent).as_posix()
                entry['review'] = (dst/'transcript.review.md').relative_to(output.parent).as_posix()
                entry['summary'] = (dst/'transcript.summary.md').relative_to(output.parent).as_posix()
                entry['topics'] = (dst/'transcript.topics.md').relative_to(output.parent).as_posix()
                if entry.get('cached'):
                    notify(f'{i}/{len(sources)} — использую готовый текст, повторная загрузка не нужна')
            _json(dst/'metadata.json', entry)
            results.append(entry)
            _json(output/'batch-results.json', results)
        except Cancelled:
            dst = output/key
            dst.mkdir(parents=True, exist_ok=True)
            entry = dict(source=source,status='cancelled',error='Задачу остановили')
            _json(dst/'metadata.json',entry)
            results.append(entry)
            _json(output/'batch-results.json',results)
            raise
        finally:
            lock.release()
        if i < len(sources) and source.startswith(('http://','https://')) and not entry.get('cached') and pause:
            if cancel.wait(pause):
                raise Cancelled('Задачу остановили')
    return results


def _check(cancel):
    if cancel.is_set():
        raise Cancelled('Задачу остановили')


def _json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _ts(value):
    ms = max(0, round(float(value) * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def _audio(url, directory, cancel):
    _check(cancel)
    ready = cached_audio(url, directory)
    if ready:
        return ready
    import yt_dlp
    from eyes import ffmpeg_path

    def progress(_):
        _check(cancel)

    opts = dict(quiet=True, no_warnings=True, noplaylist=True, socket_timeout=30,
                retries=2, extractor_retries=2, format='bestaudio/best',
                outtmpl=str(directory / 'audio.%(ext)s'),
                progress_hooks=[progress], max_filesize=2 << 30)
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        opts['ffmpeg_location'] = ffmpeg
    # Decode with Dewiar's existing transcribe_file; no lossy MP3 conversion needed.
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True) or {}
        if info.get('_type') in ('playlist', 'multi_video'):
            raise ValueError('Нужна ссылка на отдельный ролик, не плейлист')
        files = [Path(d['filepath']) for d in info.get('requested_downloads') or [] if d.get('filepath')]
        files.append(Path(y.prepare_filename(info)))
    _check(cancel)
    for path in files:
        if directory.resolve() in path.resolve().parents and path.is_file() and path.stat().st_size:
            _json(directory/'audio-cache.json', dict(source_key=source_key(url), file=path.name,
                  bytes=path.stat().st_size, info={k:info.get(k) for k in ('title','language','duration','id')}))
            return path, info
    raise RuntimeError('Загрузчик не создал аудиофайл')


def _caption_segments(tr):
    if tr.get('segments'):
        return tr['segments']
    # Older providers supply only cue starts. Keep this fact in metadata.
    lines = tr.get('lines') or []
    return [(a, max(a + .001, lines[i + 1][0] if i + 1 < len(lines) else tr.get('duration') or a + 3), text)
            for i, (a, text) in enumerate(lines)]


def _embedded(path):
    from eyes import ffmpeg_path
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return []
    result = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:s:0',
                             '-f', 'webvtt', 'pipe:1'], capture_output=True, timeout=60)
    if result.returncode:
        return []  # no subtitle stream, or bitmap subtitles requiring OCR
    return video.caption_segments(result.stdout.decode('utf-8-sig'), 'vtt')


def run_batch(sources, output, resolve, recognize, cancel=None, language='ru', force=False,
              notify=lambda message: None, pause=10, cache=None, summarize=None):
    """resolve(source)->Path, recognize(Path,language)->(segments, method, model).

    Errors belong to individual sources; a cancellation stops the batch, never publishes partial speech.
    """
    cancel = cancel or threading.Event()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if cache is not None:
        return _cached_batch(sources, output, resolve, recognize, cancel, language, force, notify, pause, Path(cache), summarize)
    results, seen = [], set()
    for source in unique_sources(sources):
        _check(cancel)
        source = str(source).strip()
        if not source or source in seen:
            continue
        seen.add(source)
        remote = source.startswith(('https://', 'http://'))
        key = source_key(source)
        directory = output / key
        directory.mkdir(parents=True, exist_ok=True)
        metadata = directory / 'metadata.json'
        entry = dict(source=source, status='processing', language=language)
        signature = None
        try:
            local = None if remote else resolve(source)
            if local:
                stat = local.stat()
                signature = [str(local.resolve()), stat.st_size, stat.st_mtime_ns]
                for ext in ('.vtt', '.srt'):
                    sidecar = local.with_suffix(ext)
                    if sidecar.is_file():
                        substat = sidecar.stat()
                        signature.append([ext, substat.st_size, substat.st_mtime_ns])
            if not force and metadata.is_file():
                old = json.loads(metadata.read_text(encoding='utf-8'))
                if ((old.get('status') == 'ok' or old.get('extraction_complete')) and old.get('requested_language') == language
                        and old.get('signature') == signature
                        and all((directory / n).is_file() and (directory / n).stat().st_size
                                for n in ('transcript.txt', 'transcript.srt', 'transcript-timed.txt','segments.json'))):
                    old['cleanup'] = ensure_clean(directory,old.get('title'),source,notify)
                    old['summary_info'] = ensure_summary(directory,old.get('title'),source,summarize,notify)
                    old['topics'] = (directory/'transcript.topics.md').relative_to(output.parent).as_posix()
                    _check(cancel)
                    old['status']='ok'
                    old.pop('error',None)
                    old['markdown'] = (directory/'transcript.clean.md').relative_to(output.parent).as_posix()
                    old['review'] = (directory/'transcript.review.md').relative_to(output.parent).as_posix()
                    old['summary'] = (directory/'transcript.summary.md').relative_to(output.parent).as_posix()
                    _json(metadata,old)
                    results.append(dict(old, cached=True))
                    _json(output / 'batch-results.json', results)
                    continue
            entry.update(requested_language=language, signature=signature)
            notify(f'Обрабатываю: {source}')
            segs = []
            if remote:
                ready = cached_audio(source, directory)
                try:
                    if ready:
                        raise video.NoTranscript('Использую уже скачанный звук')
                    tr = video.transcript(source)
                    segs = _caption_segments(tr)
                    entry.update(method='subtitles', title=tr.get('title'), language=tr.get('lang'),
                                 automatic=tr.get('auto'), cue_ends_estimated=not bool(tr.get('segments')))
                except Exception as exc:
                    _check(cancel)
                    entry['subtitle_error'] = str(exc)
                    # A rate limit is not evidence that there are no captions. Do not add more requests.
                    if any(s in str(exc).lower() for s in ('429', 'too many requests', 'confirm you', 'sign in', 'captcha')):
                        raise
                if not segs:
                    notify('Использую уже скачанный звук для распознавания' if ready else 'Субтитры не получены — скачиваю звук для распознавания')
                    local, info = _audio(source, directory, cancel)
                    entry['title'] = info.get('title') or source
                    original = (info.get('language') or '').split('-')[0]
                    if original and language != 'auto' and original != language:
                        raise ValueError(f'Язык записи {original}, а запрошен {language}. Укажи язык исходной речи.')
            else:
                entry['title'] = local.stem
                for ext in ('.vtt', '.srt'):
                    sidecar = local.with_suffix(ext)
                    if sidecar.is_file():
                        segs = video.caption_segments(sidecar.read_text(encoding='utf-8-sig'), ext[1:])
                        if segs:
                            entry.update(method='subtitles', subtitle_file=str(sidecar))
                            break
                if not segs:
                    segs = _embedded(local)
                    if segs:
                        entry.update(method='subtitles', subtitle_kind='embedded')
            _check(cancel)
            if not segs:
                notify('Распознаю речь…')
                segs, method, model = recognize(local, language)
                entry.update(method=method, model=model)
            _check(cancel)
            # Preserve optional word timings for speaker attribution. The text and
            # subtitle exports below intentionally use only the first three fields.
            segs = [(float(x[0]), float(x[1]), str(x[2]).strip(), x[3] if len(x) > 3 else None)
                    for x in segs if len(x) >= 3 and str(x[2]).strip()]
            if not segs:
                raise ValueError('Не получен текст речи; успешный результат не создан')
            text = '\n'.join(x[2] for x in segs) + '\n'
            (directory / 'transcript.txt').write_text(text, encoding='utf-8')
            (directory / 'transcript-timed.txt').write_text('\n'.join(f'[{_ts(x[0]).split(",")[0]}] {x[2]}' for x in segs) + '\n', encoding='utf-8')
            (directory / 'transcript.srt').write_text('\n'.join(f'{i}\n{_ts(x[0])} --> {_ts(x[1])}\n{x[2]}\n' for i, x in enumerate(segs, 1)), encoding='utf-8')
            _json(directory / 'segments.json', segs)
            entry.update(status='raw_ready',extraction_complete=True,characters=len(text.strip()),segments=len(segs),
                         text=(directory/'transcript.txt').relative_to(output.parent).as_posix(),
                         srt=(directory/'transcript.srt').relative_to(output.parent).as_posix())
            _json(metadata,entry)
            _check(cancel)
            entry['cleanup'] = ensure_clean(directory,entry.get('title'),source,notify)
            entry['summary_info'] = ensure_summary(directory,entry.get('title'),source,summarize,notify)
            entry['topics'] = (directory/'transcript.topics.md').relative_to(output.parent).as_posix()
            _check(cancel)
            entry.update(status='ok', characters=len(text.strip()), segments=len(segs),
                         text=(directory / 'transcript.txt').relative_to(output.parent).as_posix(),
                         srt=(directory / 'transcript.srt').relative_to(output.parent).as_posix(),
                         markdown=(directory/'transcript.clean.md').relative_to(output.parent).as_posix(),
                         review=(directory/'transcript.review.md').relative_to(output.parent).as_posix(),
                         summary=(directory/'transcript.summary.md').relative_to(output.parent).as_posix())
        except Cancelled:
            entry.update(status='cancelled', error='Задачу остановили')
            _json(metadata, entry)
            results.append(entry)
            _json(output / 'batch-results.json', results)
            raise
        except Exception as exc:
            if cancel.is_set():
                entry.update(status='cancelled', error='Задачу остановили')
                _json(metadata, entry)
                results.append(entry)
                _json(output / 'batch-results.json', results)
                raise Cancelled('Задачу остановили') from exc
            entry.update(status='error', error=str(exc))
            (directory / 'errors.log').write_text(str(exc) + '\n', encoding='utf-8')
        _json(metadata, entry)
        results.append(entry)
        _json(output / 'batch-results.json', results)
        if remote and pause and source != str(sources[-1]).strip():
            if cancel.wait(pause):
                raise Cancelled('Задачу остановили')
    return results
