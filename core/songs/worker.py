"""Isolated offline inference process. Never imports Engine/settings/TTS.

Models must already be installed. Downloads are an explicit installer action,
never a side effect of opening the Songs tab or starting transcription.
"""
import argparse
import gc
import json
import os
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


def align_phrases(audio, hypothesis, progress):
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
    bounds = [(p['start_ms']/1000,p['end_ms']/1000) for p in hypothesis['phrases']]
    for i, phrase in enumerate(hypothesis['phrases']):
        progress(f'Сверяем времена слов: {i+1}/{len(hypothesis["phrases"])}')
        start = max(0, phrase['start_ms']/1000-.4)
        end = min(len(samples)/sr, phrase['end_ms']/1000+.4)
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
                continue
            a,b = round((start+unit.start_time)*1000),round((start+unit.end_time)*1000)
            if b <= a or a < round(start*1000) or b > round(end*1000) or (words and a < words[-1]['end_ms']):
                continue
            words.append({'text':match.group(),'char_start':match.start(),'char_end':match.end(),
                          'start_ms':a,'end_ms':b,'method':'qwen3_forced_aligner','verified':False})
        if words:
            phrase.update(words=words,
                          alignment_stale=False,timing_quality='word_estimated')
            if len(words) != len(matches):
                phrase['alignment_warning'] = 'Часть слов без надёжного времени; текст сохранён полностью'
            aligned += 1
    hypothesis['alignment'] = {'model':'Qwen3-ForcedAligner-0.6B','phrases_aligned':aligned,
                               'phrases_total':len(hypothesis['phrases']),'review_required':True}
    del model
    gc.collect()


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
                    align_phrases(args.input,timed,progress)
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
    progress('Разбор завершён; требуется проверка по записи')


if __name__ == '__main__':
    main()
