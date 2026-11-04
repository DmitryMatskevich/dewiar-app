"""Launch optional music runtime, independently of the app's model settings."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def python_path():
    candidates = []
    configured = os.environ.get('DEWIAR_SONGS_PYTHON')
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path.home() / 'Library/Application Support/Dewiar/runtimes/songs/bin/python')
    if not getattr(sys, 'frozen', False):
        candidates.append(Path(__file__).resolve().parents[2] / '.songs-venv/bin/python')
    return next((p for p in candidates if p.is_file()), None)


def analyze(path, output, run_id, cancel, progress, steps='whisper,qwen,consensus,align,chords'):
    python = python_path()
    if not python:
        raise RuntimeError('Модуль полного разбора песен не установлен; доступен базовый текст')
    output = Path(output)
    log = output.with_suffix('.log')
    import shutil
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    decoded = output.with_suffix('.wav')
    decode = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y',
        '-protocol_whitelist', 'file,pipe', '-i', str(path), '-vn',
        '-c:a', 'pcm_f32le', str(decoded)], capture_output=True, timeout=90)
    if decode.returncode:
        raise ValueError('Не удалось прочитать аудио для полного разбора')
    if cancel.is_set():
        raise InterruptedError('Обработка отменена')
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               TOKENIZERS_PARALLELISM='false', PYTHONDONTWRITEBYTECODE='1')
    # PyInstaller environment must not redirect an external Python's libraries.
    for key in ('PYTHONHOME', 'PYTHONPATH', '_MEIPASS2', 'DYLD_LIBRARY_PATH'):
        env.pop(key, None)
    with log.open('w') as handle:
        process = subprocess.Popen([str(python), '-B', str(Path(__file__).with_name('worker.py')),
            '--input', str(decoded), '--output', str(output), '--run-id', run_id, '--steps', steps,
            '--baseline',str(output.parent/'gigaam.json')],
            env=env, stdout=handle, stderr=handle, stdin=subprocess.DEVNULL, start_new_session=True)
        pidfile = output.with_suffix('.pid')
        pidfile.write_text(str(process.pid), encoding='ascii')
        deadline = time.monotonic()+3600
        last = ''
        try:
            while process.poll() is None:
                if cancel.is_set():
                    raise InterruptedError('Обработка отменена')
                if time.monotonic() > deadline:
                    raise TimeoutError('Полный разбор превысил час; промежуточные результаты сохранены')
                try:
                    label = json.loads(output.with_suffix('.progress.json').read_text())['label']
                    if label != last:
                        progress(label)
                        last = label
                except (OSError, ValueError, KeyError):
                    pass
                cancel.wait(.5)
            if process.returncode:
                raise RuntimeError('Модуль разбора завершился с ошибкой; журнал: '+log.name)
            return json.loads(output.read_text())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            # Only the reproducible decoded temporary; the owned original and
            # raw hypotheses remain. Do not accumulate one WAV per rerun.
            decoded.unlink(missing_ok=True)
            if 'pidfile' in locals():
                pidfile.unlink(missing_ok=True)
