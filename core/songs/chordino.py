"""Локальный Chordino из C4DM: отдельный процесс, без моделей и сети.

Сохраняет метки смен и обращения баса. Оценку confidence не выдумываем:
CLI её не возвращает. Исходник/лицензия установлены рядом с бинарником.
"""
import os
from pathlib import Path
import re
import subprocess

from .chord_layout import clean_chord


def binary_path():
    configured = os.environ.get('DEWIAR_CHORDINO_BIN')
    path = Path(configured) if configured else Path.home()/'Library/Application Support/Dewiar/runtimes/chordino/bin/chordextract'
    return path if path.is_file() and os.access(path,os.X_OK) else None


def parse_chords(text, duration_ms):
    events=[]
    for line in text.splitlines():
        match=re.fullmatch(r'\s*(\d+(?:\.\d+)?):\s*(\S+)\s*',line)
        if not match: raise ValueError('Неожиданный формат результата Chordino')
        at=round(float(match[1])*1000)
        name='N' if match[2]=='N' else clean_chord(match[2])
        if not name or at<0 or at>duration_ms+100 or (events and at<events[-1]['start_ms']):
            raise ValueError('Некорректная смена аккорда Chordino')
        at=min(at,duration_ms)
        if events and name==events[-1]['name']:continue
        if events:events[-1]['end_ms']=at
        events.append({'name':name,'start_ms':at,'end_ms':duration_ms,
                       'method':'chordino_nnls','confidence':None,'verified':False})
    if not events:raise ValueError('Chordino не вернул результата')
    return events


def infer(path):
    import soundfile as sf
    binary=binary_path()
    if not binary:raise FileNotFoundError('Chordino не установлен')
    info=sf.info(str(path));duration=round(info.frames/info.samplerate*1000)
    env=dict(os.environ,VAMP_PATH=str(binary.parent.parent/'dictionary'))
    result=subprocess.run([str(binary),str(path)],capture_output=True,text=True,
                          timeout=300,env=env,stdin=subprocess.DEVNULL)
    if result.returncode:raise RuntimeError('Chordino: '+result.stderr[-400:])
    return {'events':parse_chords(result.stdout,duration),'method':'chordino_nnls',
            'review_required':True,'duration_ms':duration,'notation':'B=си, Bb=си-бемоль',
            'source':'https://github.com/c4dm/nnls-chroma',
            'revision':'4c5f214a75cb354f8d8c933e161377c5b4d83713'}
