"""Download media into a persisted task; success requires a nonempty output file."""
import json
from pathlib import Path


def download(url, folder, action, cancel, notify):
    import yt_dlp
    from eyes import ffmpeg_path
    from media_transcription import Cancelled
    folder.mkdir(parents=True, exist_ok=True)
    def progress(state):
        if cancel.is_set():
            raise Cancelled('Задачу остановили')
        notify('Скачиваю ' + ('звуковую дорожку' if action == 'audio' else 'видеофайл'))
    ffmpeg = ffmpeg_path()
    options = dict(quiet=True, no_warnings=True, noplaylist=True, socket_timeout=30,
                   retries=2, extractor_retries=2, max_filesize=2 << 30,
                   outtmpl=str(folder/'media.%(ext)s'), progress_hooks=[progress],
                   postprocessor_hooks=[progress],
                   format='bestaudio/best' if action == 'audio' else ('bestvideo+bestaudio/best' if ffmpeg else 'best'))
    if ffmpeg:
        options['ffmpeg_location'] = ffmpeg
    if action == 'audio':
        if not ffmpeg:
            raise ValueError('Для извлечения звукового файла нужен ffmpeg')
        options['postprocessors'] = [dict(key='FFmpegExtractAudio', preferredcodec='m4a')]
    if cancel.is_set():
        raise Cancelled('Задачу остановили')
    with yt_dlp.YoutubeDL(options) as loader:
        metadata = loader.extract_info(url, download=False) or {}
        if metadata.get('_type') in ('playlist', 'multi_video'):
            raise ValueError('Нужна ссылка на отдельный ролик; плейлисты не скачиваются автоматически')
        info = loader.extract_info(url, download=True) or {}
    if info.get('_type') in ('playlist', 'multi_video'):
        raise ValueError('Нужна ссылка на отдельный ролик; плейлисты не скачиваются автоматически')
    progress({})
    files = [p for p in folder.glob('media.*') if p.suffix not in ('.part', '.ytdl', '.json') and p.is_file() and p.stat().st_size]
    if action == 'audio':
        files = [p for p in files if p.suffix == '.m4a']
    if len(files) != 1:
        raise ValueError('Не найден готовый медиафайл; проверьте журнал загрузчика')
    return files[0], info.get('title') or url


def run(manager, task):
    from media_transcription import source_key
    rows = []
    try:
        task.data['outputs'] = []
        def notify(message):
            manager._beat(task, 'download')
            task.data['activity'] = message
        for url in task.data['link_urls']:
            directory = task.dir/'outputs/media'/source_key(url)
            marker = directory/'result.json'
            try:
                if task.cancel.is_set():
                    break
                path = None
                if marker.is_file():
                    cached = json.loads(marker.read_text(encoding='utf-8'))
                    candidate = directory/Path(cached['file']).name
                    if candidate.is_file() and candidate.stat().st_size:
                        path, title = candidate, cached['title']
                if path is None:
                    path, title = download(url, directory, task.data['link_action'], task.cancel, notify)
                    marker.write_text(json.dumps(dict(file=path.name, title=title), ensure_ascii=False), encoding='utf-8')
                manager._work(task, 'show', dict(path=path.relative_to(task.dir).as_posix(), title=title))
                rows.append(dict(url=url, status='ok', file=path.relative_to(task.dir).as_posix()))
            except Exception as error:
                rows.append(dict(url=url, status='error', error=str(error)))
            task.journal(json.dumps(rows[-1], ensure_ascii=False))
            report = task.dir/'outputs/media-results.json'
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
            task.save(); manager._emit(task)
        if (task.dir/'outputs/media-results.json').is_file():
            manager._work(task, 'show', dict(path='outputs/media-results.json', title='Отчёт загрузки медиа'))
        success = len(rows) == len(task.data['link_urls']) and all(r['status'] == 'ok' for r in rows)
        task.data.update(status='stopped' if task.cancel.is_set() else 'done' if success else 'failed',
                         finished=success, error='' if success else 'Обработка не завершена; см. отчёт и журнал',
                         result=f"Готово файлов: {sum(r['status'] == 'ok' for r in rows)} из {len(task.data['link_urls'])}.")
    except Exception as error:
        task.data.update(status='stopped' if task.cancel.is_set() else 'failed', error=str(error), finished=False)
        task.journal(str(error))
    finally:
        task.data['activity'] = ''
        manager._beat(task); task.save(); manager._emit(task)
