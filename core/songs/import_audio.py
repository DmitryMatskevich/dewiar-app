"""Bounded chunk upload: full song WAVs exceed the bridge's 16 MiB frame."""
import base64
import threading
import time
import uuid
import wave
from pathlib import Path

EXTENSIONS = {'.wav','.mp3','.m4a','.flac','.ogg','.opus','.webm','.mp4'}
LIMIT = 256*1024*1024


class AudioImporter:
    def __init__(self, store):
        self.store, self.jobs, self.lock = store, {}, threading.RLock()

    def command(self, cmd, args):
        with self.lock:
            for token, job in list(self.jobs.items()):
                if time.monotonic()-job['at'] > 1800:
                    job['path'].unlink(missing_ok=True)
                    self.jobs.pop(token)
            if cmd == 'song_upload_begin':
                song_id = str(args.get('song_id') or '')
                row = self.store.get(song_id)
                if not row or row.get('status') in {'running','recording'} or row.get('phrases'):
                    raise ValueError('Для новой записи нужна свободная карточка без текста')
                name = Path(str(args.get('name') or '')).name
                ext = Path(name).suffix.lower()
                size = args.get('size')
                if ext not in EXTENSIONS or not isinstance(size,int) or not 0 < size <= LIMIT:
                    raise ValueError('Нужен аудио/видеофайл размером до 256 МБ')
                if len(self.jobs) >= 8 or any(j['song_id']==song_id for j in self.jobs.values()):
                    raise ValueError('Дождитесь завершения текущего импорта')
                token = uuid.uuid4().hex
                path = self.store.root/song_id/'audio'/('import-'+token+ext)
                path.parent.mkdir(parents=True,exist_ok=True)
                path.touch(exist_ok=False)
                self.jobs[token] = dict(song_id=song_id,path=path,size=size,received=0,name=name,at=time.monotonic())
                return {'upload_id':token,'offset':0}
            token = str(args.get('upload_id') or '')
            job = self.jobs.get(token)
            if not job:
                raise ValueError('Импорт истёк или был отменён; выберите файл снова')
            if cmd == 'song_upload_cancel':
                job['path'].unlink(missing_ok=True)
                self.jobs.pop(token)
                return {'ok':True}
            if cmd == 'song_upload_chunk':
                if args.get('offset') != job['received']:
                    raise ValueError('Порядок фрагментов нарушен; импорт не изменён')
                encoded = args.get('data')
                if not isinstance(encoded,str) or len(encoded)>1_500_000:
                    raise ValueError('Слишком большой фрагмент')
                payload = base64.b64decode(encoded,validate=True)
                if not payload or job['received']+len(payload)>job['size']:
                    raise ValueError('Размер фрагмента не соответствует файлу')
                with job['path'].open('ab') as handle:
                    handle.write(payload)
                job.update(received=job['received']+len(payload),at=time.monotonic())
                return {'offset':job['received']}
            if cmd != 'song_upload_finish':
                raise ValueError('Неизвестная команда импорта')
            if job['received'] != job['size']:
                raise ValueError('Файл загружен не полностью')
            with self.store._lock:
                row = self.store.get(job['song_id'])
                if not row or row.get('status') in {'running','recording'} or row.get('phrases'):
                    raise ValueError('Карточка изменилась во время импорта; запись не заменена')
                audio = {'path':str(job['path'].relative_to(self.store.root)),
                         'format':job['path'].suffix[1:],'owned':True,'original_name':job['name'],'duration_ms':0}
                if job['path'].suffix == '.wav':
                    try:
                        with wave.open(str(job['path']),'rb') as wav:
                            audio.update(sample_rate=wav.getframerate(),channels=wav.getnchannels(),
                                         duration_ms=round(wav.getnframes()/wav.getframerate()*1000))
                    except (OSError,wave.Error,EOFError) as exc:
                        raise ValueError('Не удалось прочитать WAV') from exc
                saved = self.store.patch(job['song_id'],{'audio':audio,'status':'queued','stage':'lyrics',
                    'analysis_state':'audio_attached','stage_label':'Запись сохранена — можно начать разбор',
                    'capabilities':{**row.get('capabilities',{}),'audio':True},
                    'coverage':{**row.get('coverage',{}),'recorded_ms':audio['duration_ms']}})
                self.jobs.pop(token)
                return saved
