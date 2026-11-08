"""Best-effort local live transcript. Never blocks the durable PCM writer.

Only existing Vosk weights are opened; no settings, network or downloads.
This draft is UI feedback, never an analysis hypothesis or a source of timing.
"""
import json
from pathlib import Path
import queue
import threading


class CapturePreview:
    def __init__(self, models, rate, channels, publish):
        self.models, self.rate, self.channels = Path(models), rate, channels
        self.publish = publish
        self.queue = queue.Queue(maxsize=12)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name='song-live-text', daemon=True)
        self.thread.start()

    def offer(self, pcm):
        if self.stopped.is_set():return
        try:self.queue.put_nowait(pcm)
        except queue.Full:
            # Drop only preview packets; the WAV writer has already saved them.
            try:self.queue.get_nowait()
            except queue.Empty:pass
            try:self.queue.put_nowait(pcm)
            except queue.Full:pass

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=.2)

    def _run(self):
        try:
            model = self.models/'vosk-model-small-ru-0.22'
            if not (model/'am/final.mdl').is_file():
                self.publish('', 'Быстрый текст недоступен: локальная модель Vosk не установлена')
                return
            from vosk import Model, KaldiRecognizer
            import audioop
            self.publish('', 'Загружаем локальный распознаватель')
            rec = KaldiRecognizer(Model(str(model)), 16000)
            state = None; completed = ''
            self.publish('', 'Ждём слова; музыка тоже записывается')
            while not self.stopped.is_set():
                try:pcm=self.queue.get(timeout=.2)
                except queue.Empty:continue
                if self.channels == 2:pcm=audioop.tomono(pcm,2,.5,.5)
                if self.rate != 16000:pcm,state=audioop.ratecv(pcm,2,1,self.rate,16000,state)
                if rec.AcceptWaveform(pcm):
                    text=json.loads(rec.Result()).get('text','')
                    completed=(completed+' '+text).strip()[-1000:];partial=''
                else:partial=json.loads(rec.PartialResult()).get('partial','')
                self.publish((completed+' '+partial).strip()[-1200:], 'Черновое распознавание · Vosk')
        except Exception as exc:
            self.publish('', 'Быстрый текст недоступен: '+str(exc)[:140])
        finally:
            self.stopped.set()
