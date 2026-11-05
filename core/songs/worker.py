"""Isolated offline inference process. Never imports Engine/settings/TTS.

Models must already be installed. Downloads are an explicit installer action,
never a side effect of opening the Songs tab or starting transcription.
"""
import argparse
import copy
import gc
import json
import os
import re
import subprocess
from pathlib import Path
import sys
import time

# This file also runs from a signed .app. Bytecode caches must never mutate
# sealed resources inside the bundle (including when invoked directly).
sys.dont_write_bytecode = True

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def snapshot(repo):
    from huggingface_hub import snapshot_download
    manifests = [Path.home()/'Library/Application Support/Dewiar/runtimes/songs-models.json',
                 Path(__file__).resolve().parents[2]/'work/song-verification/model-install.json']
    for manifest in manifests:
        if manifest.is_file():
            entry = json.loads(manifest.read_text()).get(repo)
            if entry and (Path(entry['path'])/'config.json').is_file():
                return entry['path']
    return snapshot_download(repo, local_files_only=True)


def qwen_transcribe(audio, progress):
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_asr import Qwen3ASRModel
    # CPU float32 is conservative on Apple Silicon; MPS has unsupported ops
    # in some transformers versions. Performance is measured, not assumed.
    model = Qwen3ASRModel.from_pretrained(snapshot('Qwen/Qwen3-ASR-0.6B'),
        dtype=torch.float32, device_map='cpu', attn_implementation='eager',
        max_inference_batch_size=1, max_new_tokens=512)
    samples, sr = sf.read(audio, dtype='float32', always_2d=True)
    samples = np.mean(samples, axis=1)
    phrases = []
    chunk = sr * 25
    for start in range(0, len(samples), chunk):
        end = min(start+chunk, len(samples))
        progress(f'Qwen3: {round(start/len(samples)*100)}%')
        results = model.transcribe(audio=(samples[start:end], sr), language='Russian')
        text = results[0].text.strip()
        if text:
            phrases.append({'text': text, 'start_ms': round(start/sr*1000),
                            'end_ms': round(end/sr*1000), 'timing_quality': 'chunk'})
    del model
    gc.collect()
    return {'model': 'Qwen3-ASR-0.6B', 'input': 'mixture', 'phrases': phrases}


def whisper_transcribe(audio, run_id):
    import mlx_whisper
    import librosa
    from songs.consensus import phrases_from_words
    samples, _ = librosa.load(str(audio), sr=16000, mono=True)
    result = mlx_whisper.transcribe(samples,
        path_or_hf_repo=snapshot('mlx-community/whisper-large-v2-mlx'), language='ru',
        # Singing attention timestamps can erase complete decoded segments.
        # Decode text first; the separate align stage may add word times,
        # but must never decide whether the decoded words exist.
        word_timestamps=False, condition_on_previous_text=False,
        hallucination_silence_threshold=None, verbose=None)
    phrases = phrases_from_words(result.get('segments', []), run_id)
    return {'model': 'Whisper-large-v2-MLX', 'input': 'mixture', 'phrases': phrases,
            'decoded_segments': result.get('segments', []),
            'decoded_text': result.get('text', ''), 'text_first': True}


def _token_coverage(reference, candidate):
    """How much of a lyric phrase is independently present in another ASR span."""
    from difflib import SequenceMatcher
    from songs.consensus import normalized
    expected = [normalized(x) for x in re.findall(r'\S+', str(reference or ''))]
    observed = [normalized(x) for x in re.findall(r'\S+', str(candidate or ''))]
    expected = [x for x in expected if x]
    observed = [x for x in observed if x]
    if not expected or not observed:
        return 0.0
    matched = sum(block.size for block in SequenceMatcher(None, expected, observed, autojunk=False).get_matching_blocks())
    return matched / len(expected)


def _alignment_windows(phrases, context):
    """Broaden clipped ASR segment times only where another recognizer supports the words.

    ASR segment timestamps often include an instrumental lead-in, while another
    recognizer may keep the same phrase through the actual vocal entrance. The
    selected phrase sequence supplies hard neighboring limits so a broad chunk
    can never consume the next selected phrase.
    """
    windows = []
    for i, phrase in enumerate(phrases):
        start = max(0, int(phrase.get('start_ms') or 0))
        end = max(start + 1, int(phrase.get('end_ms') or start + 1))
        supported = []
        for hypothesis in context:
            for candidate in hypothesis.get('phrases') or []:
                if _token_coverage(phrase.get('text'), candidate.get('text')) < 0.6:
                    continue
                a, b = candidate.get('start_ms'), candidate.get('end_ms')
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a:
                    supported.append((int(a), int(b)))
        if supported:
            start = min(start, *(a for a, _ in supported))
            end = max(end, *(b for _, b in supported))
        if i:
            previous_end = int(phrases[i-1].get('end_ms') or 0)
            start = max(start, previous_end)
        if i + 1 < len(phrases):
            next_start = int(phrases[i+1].get('start_ms') or end)
            if next_start > start:
                end = min(end, next_start)
        windows.append((start, max(start + 1, end)))
    return windows


def align_phrases(audio, hypothesis, progress, context=()):
    import re
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_asr import Qwen3ForcedAligner
    from songs.consensus import normalized
    model = Qwen3ForcedAligner.from_pretrained(snapshot('Qwen/Qwen3-ForcedAligner-0.6B'),
        dtype=torch.float32, device_map='cpu', attn_implementation='eager')
    samples, sr = sf.read(audio, dtype='float32', always_2d=True)
    samples = np.mean(samples, axis=1)
    aligned = 0
    phrases = hypothesis['phrases']
    bounds = [(a/1000, b/1000) for a, b in _alignment_windows(phrases, context)]
    for i, phrase in enumerate(phrases):
        progress(f'Сверяем времена слов: {i+1}/{len(phrases)}')
        start = max(0, bounds[i][0]-.4)
        end = min(len(samples)/sr, bounds[i][1]+.4)
        if i:
            start = max(start,(bounds[i-1][1]+bounds[i][0])/2)
        if i+1 < len(bounds):
            end = min(end,(bounds[i][1]+bounds[i+1][0])/2)
        if end <= start:
            phrase['alignment_warning'] = 'Пересечение исходных границ; требуется ручная проверка'
            continue
        result = model.align(audio=(samples[round(start*sr):round(end*sr)],sr),
                             text=phrase['text'], language='Russian')[0]
        matches = list(re.finditer(r'\S+', phrase['text']))
        units = list(result)
        if len(units) != len(matches):
            phrase['alignment_warning'] = 'Выравниватель изменил число слов; оставлены времена Whisper'
            continue
        words = []
        for unit, match in zip(units,matches):
            if normalized(unit.text) != normalized(match.group()):
                words = []
                break
            a,b = round((start+unit.start_time)*1000),round((start+unit.end_time)*1000)
            if b <= a or a < round(start*1000) or b > round(end*1000) or (words and a < words[-1]['end_ms']):
                continue
            words.append({'text':match.group(),'char_start':match.start(),'char_end':match.end(),
                          'start_ms':a,'end_ms':b,'method':'qwen3_forced_aligner','verified':False})
        if words and len(words) == len(matches):
            phrase.update(words=words, alignment_stale=False, timing_quality='word_estimated')
            # Segment start can be the beginning of the file even when the
            # audio contains a long instrumental intro. A complete forced
            # alignment gives us the actual first sung word and lets the sheet
            # render intro chords as their own instrumental row.
            if i == 0 and words[0]['start_ms'] - int(phrase.get('start_ms') or 0) >= 1200:
                phrase['start_ms'] = words[0]['start_ms']
            phrase['end_ms'] = max(int(phrase.get('end_ms') or 0), words[-1]['end_ms'])
            if i == 0 and int(phrase.get('end_ms') or 0) - words[-1]['end_ms'] >= 1200:
                phrase['end_ms'] = words[-1]['end_ms']
            phrase.pop('alignment_warning', None)
            aligned += 1
        else:
            phrase['words'] = []
            phrase['alignment_stale'] = True
            phrase['timing_quality'] = 'phrase'
            phrase['alignment_warning'] = 'Выравниватель не подтвердил все слова; времена оставлены на уровне фразы'
    hypothesis['alignment'] = {'model':'Qwen3-ForcedAligner-0.6B','phrases_aligned':aligned,
                               'phrases_total':len(hypothesis['phrases']),'review_required':True}
    del model
    gc.collect()


def _separate_vocals_for_intro(audio, output, progress):
    """Use an already installed, offline separator only for a suspicious lead-in."""
    candidates = [Path.home()/'Library/Application Support/Dewiar/runtimes/separation/bin/python']
    if not getattr(sys, 'frozen', False):
        candidates.append(Path(__file__).resolve().parents[2]/'.songs-separation-venv/bin/python')
    python = next((p for p in candidates if p.is_file()), None)
    if python is None:
        return None
    probe = (Path(sys._MEIPASS)/'tools/song_separation_probe.py' if getattr(sys, 'frozen', False)
             else Path(__file__).resolve().parents[2]/'tools/song_separation_probe.py')
    if not probe.is_file():
        return None
    folder = Path(output).parent/'intro-vocal-check'
    folder.mkdir(parents=True, exist_ok=True)
    progress('Проверяем начало вокала по отделённому голосу')
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1')
    for key in ('PYTHONHOME', 'PYTHONPATH', 'DYLD_LIBRARY_PATH', '_MEIPASS2'):
        env.pop(key, None)
    log_path = folder/'separator.log'
    try:
        with log_path.open('w', encoding='utf-8') as log:
            proc = subprocess.run([str(python), '-B', str(probe), str(audio), str(folder)],
                                  stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  env=env, timeout=1200)
        vocals = folder/'vocals.wav'
        if proc.returncode == 0 and vocals.is_file() and vocals.stat().st_size:
            return vocals
    except (OSError, subprocess.SubprocessError):
        pass
    import shutil
    shutil.rmtree(folder, ignore_errors=True)
    return None


def align_intro_from_vocals(audio, output, hypothesis, context, progress, original_start_ms=None):
    """Retry a false zero-second first phrase on vocal-only audio, if available offline."""
    phrases = hypothesis.get('phrases') or []
    if not phrases:
        return False
    first = phrases[0]
    original_start = int(first.get('start_ms') or 0) if original_start_ms is None else int(original_start_ms)
    if original_start > 250:
        return False
    window = _alignment_windows(phrases, context)[0]
    current_words = first.get('words') or []
    first_word_start = current_words[0].get('start_ms') if current_words else None
    needs_retry = (first_word_start is None or first_word_start - original_start < 1500
                   or window[1] - int(first.get('end_ms') or 0) >= 1200)
    if not needs_retry:
        return False
    vocals = _separate_vocals_for_intro(audio, output, progress)
    if vocals is None:
        return False
    candidate = copy.deepcopy(first)
    candidate['start_ms'], candidate['end_ms'] = window
    retry = {'phrases': [candidate]}
    align_phrases(vocals, retry, progress)
    aligned = retry['phrases'][0]
    if (aligned.get('timing_quality') != 'word_estimated' or
            not aligned.get('words') or aligned['words'][0]['start_ms'] - original_start < 1500):
        import shutil
        shutil.rmtree(vocals.parent, ignore_errors=True)
        return False
    first.clear()
    first.update(aligned)
    hypothesis.setdefault('alignment', {})['intro_vocal_check'] = {
        'source': 'HTDemucs vocal stem', 'start_ms': aligned['start_ms'],
        'end_ms': aligned['end_ms']}
    import shutil
    shutil.rmtree(vocals.parent, ignore_errors=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', default='worker')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--steps', default='whisper,qwen,consensus,align,chords')
    args = parser.parse_args()
    progress_path = args.output.with_suffix('.progress.json')
    def progress(label):
        save(progress_path, {'label': label, 'at': time.time()})
    result = json.loads(args.output.read_text()) if args.resume and args.output.is_file() else {'hypotheses': [], 'errors': [], 'harmony': None}
    result['completed'] = False
    result['errors'] = [e for e in result.get('errors', []) if e.get('step') not in args.steps.split(',')]
    for step in args.steps.split(','):
        start = time.monotonic()
        try:
            progress({'whisper': 'Whisper large-v2: слова и времена',
                      'qwen': 'Qwen3: независимый вариант текста',
                      'align': 'Уточняем времена слов',
                      'consensus': 'Сверяем слова трёх распознавателей',
                      'chords': 'Определяем смены аккордов по звуку'}[step])
            if step == 'whisper':
                hyp = whisper_transcribe(args.input, args.run_id)
                hyp['seconds'] = round(time.monotonic()-start, 2)
                result['hypotheses'] = [h for h in result['hypotheses'] if h['model'] != hyp['model']]
                result['hypotheses'].append(hyp)
            elif step == 'qwen':
                hyp = qwen_transcribe(args.input, progress)
                hyp['seconds'] = round(time.monotonic()-start, 2)
                result['hypotheses'] = [h for h in result['hypotheses'] if h['model'] != hyp['model']]
                result['hypotheses'].append(hyp)
            elif step == 'chords':
                from songs.harmony import infer_chords
                result['harmony'] = infer_chords(args.input)
            elif step == 'align':
                timed = result.get('selected')
                if timed:
                    context = list(result.get('hypotheses') or [])
                    if args.baseline and args.baseline.is_file():
                        context.insert(0, json.loads(args.baseline.read_text()))
                    initial_start = (timed.get('phrases') or [{}])[0].get('start_ms', 0)
                    align_phrases(args.input,timed,progress,context)
                    align_intro_from_vocals(args.input,args.output,timed,context,progress,initial_start)
            elif step == 'consensus':
                from songs.consensus import consensus, choose_spine, recover_omissions
                hypotheses = list(result['hypotheses'])
                if args.baseline and args.baseline.is_file():
                    hypotheses.insert(0,json.loads(args.baseline.read_text()))
                timed = next((h for h in hypotheses if h['model'].startswith('Whisper') and h.get('phrases')),None)
                base = choose_spine(hypotheses)
                if base:
                    result['selected'] = {'model':'conservative_word_vote', 'phrases':consensus(
                        recover_omissions(base['phrases'], hypotheses),[h for h in hypotheses if h is not base],choose_majority=True)}
        except Exception as exc:
            result['errors'].append({'step': step, 'error': str(exc)[:1000]})
        save(args.output, result)
        gc.collect()
    result['completed'] = True
    save(args.output, result)
    progress('Разбор завершён; требуется проверка по записи')


if __name__ == '__main__':
    main()
