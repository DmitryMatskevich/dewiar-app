"""Fit saved songs to their vocal stems: intro, breaks and line edges.

The same code as the batch worker (``core/songs/vocal_timing.py``), applied to
cards that already exist. Text, chords, manual boundaries and manual word
times are kept; only edges which the vocal stem shows inside a break move.

Default is a dry run with a report. ``--apply`` saves a backup of every card
it changes (``<song>/audio/analysis/<uuid>/before-vocal-timing.json``) and
writes with a revision check, so a card edited meanwhile is skipped.

  ~/Documents/dewiar/app/.songs-venv/bin/python tools/songs_vocal_timing.py
  ~/Documents/dewiar/app/.songs-venv/bin/python tools/songs_vocal_timing.py --apply
  ... --song <id>        only this card
  ... --separate         make a temporary stem when the card has no karaoke stem
  ... --recover-text     also read «singing without text» again with Whisper and
                         Qwen; a line both models agree on is added (needs the
                         song models; run with .songs-venv/bin/python)
  ... --realign-grid     lines whose recognizer times were a whole-second grid
                         (30–32, 32–34 … s) are aligned again as one passage
                         (worker.realign_grid_runs; needs the song models)
"""
import argparse
import copy
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'core'))

from songs import vocal_timing as vt  # noqa: E402
from songs.chord_layout import build_guitar_sheet  # noqa: E402
from songs.onset_check import existing_vocals  # noqa: E402
from songs.store import SongStore  # noqa: E402

ROOT = Path.home() / 'Library/Application Support/Dewiar/state/songs'


def separate(audio, folder):
    python = next((p for p in (Path.home() / 'Library/Application Support/Dewiar/runtimes/separation/bin/python',
                               APP / '.songs-separation-venv/bin/python') if p.is_file()), None)
    probe = APP / 'tools/song_separation_probe.py'
    if python is None or not probe.is_file():
        return None
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
    for key in ('PYTHONHOME', 'PYTHONPATH', 'DYLD_LIBRARY_PATH'):
        env.pop(key, None)
    with (folder / 'separator.log').open('w') as log:
        code = subprocess.run([str(python), '-B', str(probe), str(audio), str(folder)], stdout=log,
                              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, timeout=1800).returncode
    vocals = folder / 'vocals.wav'
    return vocals if code == 0 and vocals.is_file() and vocals.stat().st_size else None


def load_activity(path, duration_ms):
    import numpy as np
    try:
        import soundfile as sf
        samples, sr = sf.read(str(path), dtype='float32', always_2d=True)
        mono = np.mean(samples, axis=1)
    except ImportError:
        import shutil
        raw = subprocess.check_output([shutil.which('ffmpeg') or 'ffmpeg', '-v', 'error', '-nostdin', '-i', str(path),
                                       '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'], timeout=300)
        mono, sr = np.frombuffer(raw, dtype='<f4'), 16000
    return vt.activity(vt.envelope(mono, sr), duration_ms or round(len(mono) / sr * 1000))


def recover_text(audio, phrases, activity, hypotheses, title):
    """Decode the card's recording to WAV and run the worker's two-model recheck."""
    import shutil
    from songs.worker import recover_sung_gaps
    ffmpeg = shutil.which('ffmpeg') or next((p for p in ('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg')
                                             if Path(p).is_file()), None)
    if ffmpeg is None:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix='dewiar-sung-text-') as tmp:
        wav = Path(tmp) / 'audio.wav'
        subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-i', str(audio), '-vn', '-c:a', 'pcm_f32le',
                        str(wav)], check=True, timeout=300)
        hypothesis = {'phrases': copy.deepcopy(phrases)}
        try:
            added = recover_sung_gaps(wav, hypothesis, activity, hypotheses,
                                      lambda label: print(f'  … {title}: {label}', flush=True),
                                      run_id=f'tool-{uuid.uuid4().hex[:8]}')
        except Exception as exc:  # keep the timing fix even if a model is missing
            print(f'  ! {title}: проверка текста не выполнена — {exc}', flush=True)
            return 0, phrases, {'error': str(exc)[:300]}
    return added, hypothesis['phrases'], hypothesis.get('sung_gap_recovery')


def _wav(audio, tmp):
    import shutil
    ffmpeg = shutil.which('ffmpeg') or next((p for p in ('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg')
                                             if Path(p).is_file()), None)
    if ffmpeg is None:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    wav = Path(tmp) / 'audio.wav'
    subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-i', str(audio), '-vn', '-c:a', 'pcm_f32le',
                    str(wav)], check=True, timeout=300)
    return wav


def realign_grid(audio, phrases, activity, hypotheses, title, vocals=None):
    """Run the worker's joint alignment for passages timed on a whole-second grid."""
    from songs.worker import realign_grid_runs
    with tempfile.TemporaryDirectory(prefix='dewiar-grid-') as tmp:
        hypothesis = {'phrases': copy.deepcopy(phrases)}
        try:
            changed = realign_grid_runs(_wav(audio, tmp), hypothesis, activity, hypotheses,
                                        lambda label: print(f'  … {title}: {label}', flush=True), vocals=vocals)
        except Exception as exc:
            print(f'  ! {title}: повторное выравнивание не выполнено — {exc}', flush=True)
            return 0, phrases, {'error': str(exc)[:300]}
    return changed, hypothesis['phrases'], hypothesis.get('grid_realignment')


def sections_for(sections, phrases):
    out = copy.deepcopy(sections or [])
    for section in out:
        members = [phrases[i] for i in section.get('phrase_indexes', []) if 0 <= i < len(phrases)]
        if members:
            section.update(start_ms=min(p['start_ms'] for p in members), end_ms=max(p['end_ms'] for p in members))
    return out


def seconds(ms):
    return '—' if ms is None else f'{ms / 1000:.1f} с'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--song', action='append', default=[])
    parser.add_argument('--separate', action='store_true')
    parser.add_argument('--recover-text', action='store_true')
    parser.add_argument('--realign-grid', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--report', type=Path,
                        default=APP / 'work' / f'vocal-timing-{dt.datetime.now():%Y%m%d-%H%M%S}.json')
    args = parser.parse_args()
    store = SongStore(args.root)
    rows = [store.get(sid) for sid in (args.song or [p.parent.name for p in sorted(args.root.glob('*/song.json'))])]
    report = []
    for row in rows:
        if not row:
            continue
        title = row.get('title') or row['song_id']
        item = {'song_id': row['song_id'], 'title': title, 'revision': row.get('revision')}
        report.append(item)
        if row.get('status') in {'running', 'recording'} or row.get('active_run_id'):
            item['skipped'] = 'идёт обработка'
            print(f'— {title}: пропущена, идёт обработка')
            continue
        audio = (args.root / str((row.get('audio') or {}).get('path') or '')).resolve()
        if not audio.is_file() or not row.get('phrases'):
            item['skipped'] = 'нет записи или текста'
            print(f'— {title}: пропущена, нет записи или текста')
            continue
        duration = (row.get('audio') or {}).get('duration_ms')
        vocals, temp = existing_vocals(audio), None
        if vocals is None and args.separate:
            temp = tempfile.TemporaryDirectory(prefix='dewiar-vocal-timing-')
            print(f'… {title}: отделяем голос', flush=True)
            vocals = separate(audio, Path(temp.name))
        if vocals is None:
            item['skipped'] = 'нет вокальной дорожки (минусовки); запустите с --separate'
            print(f'— {title}: нет вокальной дорожки')
            continue
        try:
            activity = load_activity(vocals, duration)
        finally:
            if temp:
                temp.cleanup()
                vocals = None
        if activity is None:
            item['skipped'] = 'дорожка голоса без ясного контраста'
            print(f'— {title}: голос не отделяется от музыки, оставлена как есть')
            continue
        selected = next((r for r in row.get('analysis_runs') or [] if r.get('run_id') == row.get('selected_run_id')), {})
        hypotheses = selected.get('hypotheses') or []
        before = vt.summary(row['phrases'], dict(activity, lexical=vt.unrecognized_windows(row['phrases'], hypotheses)),
                            duration)
        fitted, fit = vt.fit_phrases(row['phrases'], activity)
        added = regridded = 0
        if args.realign_grid and vt.grid_groups(fitted, vt.grid_segments(hypotheses)):
            regridded, fitted, grid = realign_grid(audio, fitted, activity, hypotheses, title, vocals)
            item['grid_realignment'] = grid
            if regridded:
                moved = [(p.get('text'), p.get('grid_source_ms'), [p.get('start_ms'), p.get('end_ms')])
                         for p in fitted if p.get('grid_realigned')]
                for text, old_span, new_span in moved:
                    print(f'    {str(text)[:48]!r}: {seconds(old_span[0])} → {seconds(new_span[0])}–{seconds(new_span[1])}', flush=True)
            else:
                for run in (grid or {}).get('runs', []):
                    print(f"  — {title}: отрывок {seconds(run['window_ms'][0])}–{seconds(run['window_ms'][1])} "
                          f"не переставлен: {run.get('reason') or run.get('attempts')}", flush=True)
        # A card whose word times contradict its own lines (overlapping
        # recognitions) needs a full reprocessing; adding lines would only
        # pile more text onto it.
        if args.recover_text and fit.get('skipped'):
            item['text_recovery'] = {'skipped': 'inconsistent_card'}
        elif args.recover_text and vt.sung_gaps(fitted, dict(activity, lexical=vt.unrecognized_windows(fitted, hypotheses)),
                                                duration):
            added, fitted, recovery = recover_text(audio, fitted, activity, hypotheses, title)
            item['text_recovery'] = recovery
        after = vt.summary(fitted, dict(activity, lexical=vt.unrecognized_windows(fitted, hypotheses)), duration)
        changed = [c for p in fit.get('phrases', []) for c in p['changes']]
        item.update(before=before, after=after, changes=fit.get('phrases', []))
        print(f"{'✓' if not changed else '•'} {title}: голос с {seconds(after['first_voice_ms'])}, "
              f"первая строка {seconds(before['first_lyric_ms'])} → {seconds(after['first_lyric_ms'])}; "
              f"проигрышей {len(after['breaks'])}; пение без текста {len(after['voiced_gaps'])}; "
              f"текст без голоса {len(after['unvoiced_lyrics'])}; правок времени {len(changed)}"
              + (f"; пропущено несогласованных строк {fit['skipped']}" if fit.get('skipped') else '')
              + (f"; добавлено строк по двум распознавателям {added}" if added else '')
              + (f"; заново выровнено отрывков с сеточным временем {regridded}" if regridded else ''), flush=True)
        if not args.apply:
            continue
        if not changed and not added and not regridded and row.get('vocal_activity') == activity:
            continue
        backup = args.root / row['song_id'] / 'audio' / 'analysis' / str(uuid.uuid4())
        backup.mkdir(parents=True, exist_ok=False)
        (backup / 'before-vocal-timing.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            store.patch(row['song_id'], {
                'phrases': fitted, 'words': [w for p in fitted for w in p.get('words', [])],
                'sections': sections_for(vt.remap_sections(row.get('sections'), row['phrases'], fitted), fitted),
                'vocal_activity': activity, 'timing_version': vt.VERSION,
                'guitar_sheet': build_guitar_sheet(fitted, row.get('chords') or [], duration, activity),
            }, row['revision'])
            item['applied'] = True
            item['backup'] = str(backup)
        except ValueError as exc:
            item['applied'] = False
            item['error'] = str(exc)
            print(f'  ! {title}: не записано — {exc}')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n{'Записано' if args.apply else 'Проверка без записи'}; отчёт: {args.report}")


if __name__ == '__main__':
    main()
