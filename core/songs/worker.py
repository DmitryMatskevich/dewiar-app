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

_qwen_cache = None
_qwen_cache_repo = None


def release_qwen_model():
    global _qwen_cache, _qwen_cache_repo
    _qwen_cache = _qwen_cache_repo = None
    gc.collect()


def qwen_model(repo):
    """Reuse only the weights within one worker; each crop is a new decoding."""
    global _qwen_cache, _qwen_cache_repo
    if repo not in ('Qwen/Qwen3-ASR-0.6B','Qwen/Qwen3-ASR-1.7B'):
        raise ValueError('Неизвестная модель Qwen для песен')
    if _qwen_cache is None or _qwen_cache_repo != repo:
        release_qwen_model()
        import torch
        from qwen_asr import Qwen3ASRModel
        _qwen_cache=Qwen3ASRModel.from_pretrained(snapshot(repo),
            dtype=torch.float32,device_map='cpu',attn_implementation='eager',
            max_inference_batch_size=1,max_new_tokens=512)
        _qwen_cache_repo=repo
    return _qwen_cache


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


def qwen_transcribe(audio, progress, windows=None, model_repo='Qwen/Qwen3-ASR-0.6B'):
    import numpy as np
    import soundfile as sf
    # CPU float32 is conservative on Apple Silicon; MPS has unsupported ops
    # in some transformers versions. Performance is measured, not assumed.
    model = qwen_model(model_repo)
    samples, sr = sf.read(audio, dtype='float32', always_2d=True)
    samples = np.mean(samples, axis=1)
    phrases = []
    if windows is None:
        windows = [(start, min(start+25000, round(len(samples)/sr*1000)))
                   for start in range(0, round(len(samples)/sr*1000), 25000)]
    for start_ms, end_ms in windows:
        start = max(0, round(start_ms/1000*sr))
        end = min(round(end_ms/1000*sr), len(samples))
        if end <= start:
            continue
        progress(f'Qwen3: {round(start/len(samples)*100)}%')
        results = model.transcribe(audio=(samples[start:end], sr), language='Russian')
        text = results[0].text.strip()
        if text:
            phrases.append({'phrase_id': f'qwen-{start_ms}-{end_ms}',
                            'text': text, 'start_ms': round(start/sr*1000),
                            'end_ms': round(end/sr*1000), 'timing_quality': 'chunk'})
    return {'model': model_repo.split('/')[-1], 'input': 'mixture', 'phrases': phrases}


def whisper_transcribe(audio, run_id, start_ms=0, end_ms=None):
    import mlx_whisper
    import librosa
    from songs.consensus import phrases_from_words
    samples, _ = librosa.load(str(audio), sr=16000, mono=True, offset=start_ms/1000,
                            duration=None if end_ms is None else (end_ms-start_ms)/1000)
    result = mlx_whisper.transcribe(samples,
        path_or_hf_repo=snapshot('mlx-community/whisper-large-v2-mlx'), language='ru',
        # Singing attention timestamps can erase complete decoded segments.
        # Decode text first; the separate align stage may add word times,
        # but must never decide whether the decoded words exist.
        word_timestamps=False, condition_on_previous_text=False,
        hallucination_silence_threshold=None, verbose=None)
    phrases = phrases_from_words(result.get('segments', []), run_id)
    for phrase in phrases:
        phrase['start_ms'] += start_ms
        phrase['end_ms'] += start_ms
    return {'model': 'Whisper-large-v2-MLX', 'input': 'mixture', 'phrases': phrases,
            'decoded_segments': result.get('segments', []),
            'decoded_text': result.get('text', ''), 'text_first': True}


def repair_disagreements(audio, base, hypotheses, progress, max_windows=None, previous=None, checkpoint=None):
    from songs.reconciliation import (propose_repairs, confirms, confirm_coverage,
                                      apply_repairs, family, fresh_agreements, boundary_windows, protect_prior_agreement,
                                      quarantine_credit_phrases)
    proposals = propose_repairs(base['phrases'], [h for h in hypotheses if h is not base])
    report = {'algorithm': 'time_local_audio_consensus_v2', 'proposals': proposals,
              'rechecks': [], 'errors': [], 'max_windows': max_windows}
    # A user-selected single-model comparison must not load other ASR models.
    if not {'whisper', 'qwen'} <= {family(h['model']) for h in hypotheses}:
        report['reason'] = 'Для проверки спорных мест выберите Whisper и Qwen'
        return copy.deepcopy(base['phrases']), report
    import hashlib
    digest=hashlib.sha256()
    with open(audio,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    report['audio_sha256']=digest.hexdigest()
    qwen_repo=('Qwen/Qwen3-ASR-1.7B' if any(h['model']=='Qwen3-ASR-1.7B' for h in hypotheses)
               else 'Qwen/Qwen3-ASR-0.6B')
    report['qwen_model']=qwen_repo
    cache={}
    if (previous or {}).get('audio_sha256')==report['audio_sha256']:
        cache={(r['start_ms'],r['end_ms']):r for r in previous.get('rechecks',[])
               if r.get('hypotheses')}
    visited=set()
    for sweep in range(2):
        windows=[]
        for p in proposals:
            if len(set(p.get('confirmed_models',[])))>=2:continue
            a,b=p['start_ms'],p['end_ms']
            candidates=[(a,b)] if sweep==0 else (
                [(t,min(t+16000,b)) for t in range(a,max(a+1,b-8000),8000)]
                if b-a>16000 and not p.get('coverage') else [(max(0,a-3000),b+3000)])
            for window in candidates:
                if window not in visited and window not in windows:windows.append(window)
        if sweep==0:
            for window in boundary_windows(hypotheses):
                if window not in windows:windows.append(window)
        for index,(start,end) in enumerate(windows):
            if max_windows is not None and len(visited)>=max_windows:break
            visited.add((start,end))
            progress(f'Проверка звука {sweep+1}/2: участок {index+1}/{len(windows)}')
            evidence=copy.deepcopy(cache.get((start,end),{}).get('hypotheses',[]))
            if (previous or {}).get('qwen_model','Qwen/Qwen3-ASR-0.6B')!=qwen_repo:
                evidence=[h for h in evidence if family(h['model'])!='qwen']
            for model in ('whisper','qwen'):
                if model not in {family(h['model']) for h in evidence}:
                    try:
                        if model=='whisper':
                            h=whisper_transcribe(audio,f'recheck-{sweep}-{index}',start,end)
                        elif qwen_repo.endswith('1.7B'):
                            h=qwen_transcribe(audio,progress,[(start,end)],model_repo=qwen_repo)
                        else:
                            h=qwen_transcribe(audio,progress,[(start,end)])
                        evidence.append(h)
                    except Exception as exc:
                        report['errors'].append({'model':model,'start_ms':start,'error':str(exc)[:500]})
            report['rechecks'].append({'start_ms':start,'end_ms':end,'hypotheses':evidence})
            for p in proposals:
                if len(p.get('confirmed_models',[]))>=2:continue
                if start<p['end_ms'] and end>p['start_ms']:
                    if p.get('coverage'):
                        # A wider crop includes adjacent lyrics. Without word
                        # times they cannot safely replace this whole block.
                        if start==p['start_ms'] and end==p['end_ms']:confirm_coverage(p,evidence)
                    else:
                        confirmed=[h['model'] for h in evidence if confirms(p,h)]
                        if len(confirmed)>len(p.get('confirmed_models',[])):
                            p['confirmed_models']=confirmed
            if checkpoint:checkpoint(report)
    keys={(p['begin'],p['end'],tuple(p['context'])) for p in proposals}
    for p in fresh_agreements(base['phrases'],report['rechecks']):
        key=(p['begin'],p['end'],tuple(p['context']))
        if key not in keys:
            proposals.append(p);keys.add(key)
    protect_prior_agreement(base,hypotheses,proposals)
    repaired = apply_repairs(base['phrases'], proposals)
    repaired,report['quarantined_phrases']=quarantine_credit_phrases(repaired,hypotheses,report['rechecks'])
    report['accepted'] = sum(p['status'] == 'two_model_audio_agreement' for p in proposals)
    report['needs_review'] = len(proposals)-report['accepted']
    return repaired, report


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
                    # Repeated choruses elsewhere in the recording cannot
                    # widen the search window of this particular occurrence.
                    if a >= end+2000 or b <= start-2000:
                        continue
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


def align_phrases(audio, hypothesis, progress, context=(), vocal_path=None, vocal_factory=None):
    release_qwen_model()
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
    from songs.phrase_timeline import ordered_phrases, alignment_groups
    originals = ordered_phrases(hypothesis['phrases'], context)
    groups = alignment_groups(originals)
    phrases = []
    for group in groups:
        combined = copy.deepcopy(group[0])
        combined.update(text=' '.join(p['text'] for p in group),
                        start_ms=min(p.get('recovery_window',p)['start_ms'] for p in group),
                        end_ms=max(p['end_ms'] for p in group))
        phrases.append(combined)
    from songs.onset_check import existing_vocals, retry_long_onset, retry_stem_alignment, suspicious_onset
    vocal_path = vocal_path or existing_vocals(audio)
    vocal_audio = None
    def load_vocals(path):
        if not path:
            return None
        vx, vsr = sf.read(str(path), dtype='float32', always_2d=True)
        if abs(len(vx)/vsr-len(samples)/sr) < .1:
            return (np.mean(vx,axis=1),vsr)
        return None
    vocal_audio = load_vocals(vocal_path)
    tried_vocal_factory = False
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
        matches = [m for m in re.finditer(r'\S+', phrase['text']) if normalized(m.group())]
        units = list(result)
        from songs.alignment_words import measured_words
        words = measured_words(phrase['text'], units, round(start*1000), round(end*1000))
        if (vocal_audio is None and vocal_factory and not tried_vocal_factory and
                (len(words)<len(matches) or suspicious_onset(words,None))):
            tried_vocal_factory = True
            vocal_audio = load_vocals(vocal_factory())
        words, stem_check = retry_stem_alignment(model, phrase['text'], words,
                                                round(start*1000), round(end*1000), vocal_audio)
        if stem_check:
            phrase['vocal_alignment_check'] = stem_check
        words, onset_check = retry_long_onset(model, samples, sr, phrase['text'], words,
                                             round(end*1000), vocal_audio)
        if onset_check:
            phrase['onset_check'] = onset_check
        if words:
            # A contextual crop can start before the coarse ASR segment.
            # Retain every measured word inside its parent phrase, including
            # partial alignments and the last sung word near a segment edge.
            phrase['start_ms']=min(int(phrase.get('start_ms') or 0),words[0]['start_ms'])
            phrase['end_ms']=max(int(phrase.get('end_ms') or 0),words[-1]['end_ms'])
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
        elif words:
            phrase.update(words=words,alignment_stale=False,timing_quality='word_partial',
                alignment_warning='Часть слов имеет измеренные времена; остальные подсвечиваются приблизительно')
        else:
            phrase['words'] = []
            phrase['alignment_stale'] = True
            phrase['timing_quality'] = 'phrase'
            phrase['alignment_warning'] = 'Выравниватель не подтвердил все слова; времена оставлены на уровне фразы'
    # Return timings to the original phrase IDs/character spans, including
    # partial words. Tighten each measured edge, not just the song's first row.
    for combined, group in zip(phrases,groups):
        offset = 0
        for phrase in group:
            text = phrase['text']
            words = [dict(w,char_start=w['char_start']-offset,char_end=w['char_end']-offset)
                     for w in combined.get('words',[]) if offset <= w['char_start']
                     and w['char_end'] <= offset+len(text)]
            if words:
                phrase.update(words=words,alignment_stale=False,
                    timing_quality='word_estimated' if len(words)==len(re.findall(r'\S+',text)) else 'word_partial')
                if words[0]['char_start']==0:
                    phrase['start_ms']=words[0]['start_ms']
                if words[-1]['char_end']==len(text):
                    phrase['end_ms']=words[-1]['end_ms']
                if combined.get('onset_check'):
                    phrase['onset_check']=copy.deepcopy(combined['onset_check'])
                if combined.get('vocal_alignment_check'):
                    phrase['vocal_alignment_check']=copy.deepcopy(combined['vocal_alignment_check'])
                if phrase['timing_quality']=='word_partial':
                    phrase['alignment_warning']='Часть слов имеет измеренные времена; остальные подсвечиваются приблизительно'
                else:
                    phrase.pop('alignment_warning',None)
            else:
                phrase.update(words=[],alignment_stale=True,timing_quality='phrase',
                    alignment_warning=combined.get('alignment_warning','Не удалось уточнить времена слов'))
            offset += len(text)+1
    hypothesis['phrases'] = originals
    aligned = sum(p.get('timing_quality') == 'word_estimated' for p in originals)
    partial = sum(p.get('timing_quality') == 'word_partial' for p in originals)
    hypothesis['alignment'] = {'model':'Qwen3-ForcedAligner-0.6B','phrases_aligned':aligned,
                               'phrases_partially_aligned':partial,
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
    progress('Готовим вокальную дорожку для проверки вступления и проигрышей')
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


def _vocal_onset_ms(vocals):
    """Estimate the first sung moment from a separated vocal stem.

    This is deliberately conservative: a few consecutive energetic windows are
    required, and the first 250 ms are ignored so separator leakage cannot move
    a phrase to the beginning of the file.
    """
    import numpy as np
    import soundfile as sf
    samples, sr = sf.read(str(vocals), dtype='float32', always_2d=True)
    samples = np.mean(samples, axis=1)
    if not len(samples):
        return 0
    hop = max(1, round(sr * .05))
    frame = max(hop, round(sr * .10))
    rms = []
    for start in range(0, max(1, len(samples) - frame + 1), hop):
        block = samples[start:start + frame]
        rms.append(float(np.sqrt(np.mean(block * block) + 1e-12)))
    if not rms:
        return 0
    noise = float(np.percentile(rms[:max(1, min(len(rms), round(1.0 * sr / hop)))], 70))
    threshold = max(noise * 2.4, float(np.percentile(rms, 55)) * .75, 1e-4)
    needed = max(2, round(.15 * sr / hop))
    first = max(0, round(.25 * sr / hop))
    for index in range(first, max(first + 1, len(rms) - needed + 1)):
        if all(value >= threshold for value in rms[index:index + needed]):
            return round(index * hop * 1000 / sr)
    return 0


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
        onset = _vocal_onset_ms(vocals)
        if original_start + 1200 <= onset < int(first.get('end_ms') or 0):
            # Stem energy alone is not a lexical onset: guitar leakage or a
            # loud second line can be mistaken for the first sung syllable.
            # Keep it as review evidence; never cut off already decoded words.
            first['alignment_warning'] = 'Начало вокала требует проверки по записи'
            hypothesis.setdefault('alignment', {})['intro_vocal_check'] = {
                'source': 'HTDemucs vocal stem onset', 'suggested_start_ms': onset,
                'applied': False,
                'end_ms': int(first.get('end_ms') or 0)}
            import shutil
            shutil.rmtree(vocals.parent, ignore_errors=True)
            return False
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


def verify_reference_candidate(audio, phrase, proposal, hypotheses, progress):
    """Independent, unprompted audio decoding; the publication never enters ASR."""
    from songs.reconciliation import family
    from songs.reference_text import tokens
    if not {'whisper','qwen'} <= {family(h.get('model','')) for h in hypotheses}:
        return {'confirmed':False,'reason':'Для проверки нужны выбранные Whisper и Qwen'}
    start,end=phrase.get('start_ms',0),phrase.get('end_ms',0)
    if not 0<end-start<=30000:
        return {'confirmed':False,'reason':'Фраза требует ручной проверки границ'}
    cur=proposal['current'];ts=tokens(phrase['text'])
    left=[t['norm'] for t in ts if t['end']<=cur['char_start']][-2:]
    right=[t['norm'] for t in ts if t['start']>=cur['char_end']][:2]
    needle=left+[t['norm'] for t in tokens(proposal['alternative']['text'])]+right
    if len(left)<2 or len(right)<2:return {'confirmed':False,'reason':'Недостаточно контекста'}
    repo='Qwen/Qwen3-ASR-1.7B' if any(h.get('model')=='Qwen3-ASR-1.7B' for h in hypotheses) else 'Qwen/Qwen3-ASR-0.6B'
    decoded=[whisper_transcribe(audio,'reference-check',start,end),
             qwen_transcribe(audio,progress,[(start,end)],model_repo=repo)]
    evidence=[]
    for hyp in decoded:
        text=' '.join(p['text'] for p in hyp.get('phrases',[]));words=[t['norm'] for t in tokens(text)]
        match=any(words[i:i+len(needle)]==needle for i in range(len(words)-len(needle)+1))
        evidence.append({'model':hyp['model'],'text':text,'matches':match})
    return {'confirmed':all(h['matches'] for h in evidence),'hypotheses':evidence,'start_ms':start,'end_ms':end}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', default='worker')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--steps', default='whisper,qwen_large,consensus,text_reference,align,structure,chords')
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
                      'qwen_large': 'Qwen3-ASR 1.7B: независимый вариант текста',
                      'align': 'Уточняем времена слов',
                      'structure': 'Размечаем куплеты и припевы',
                      'consensus': 'Сравниваем распознавания с исходной записью',
                      'text_reference': 'Сверка с источником текста: ищем публикации',
                      'chords': 'Определяем смены аккордов по звуку'}[step])
            if step == 'whisper':
                hyp = whisper_transcribe(args.input, args.run_id)
                hyp['seconds'] = round(time.monotonic()-start, 2)
                result['hypotheses'] = [h for h in result['hypotheses'] if h['model'] != hyp['model']]
                result['hypotheses'].append(hyp)
            elif step in ('qwen','qwen_large'):
                hyp = qwen_transcribe(args.input, progress,model_repo=(
                    'Qwen/Qwen3-ASR-1.7B' if step=='qwen_large' else 'Qwen/Qwen3-ASR-0.6B'))
                hyp['seconds'] = round(time.monotonic()-start, 2)
                result['hypotheses'] = [h for h in result['hypotheses'] if not h['model'].startswith('Qwen3-ASR')]
                result['hypotheses'].append(hyp)
            elif step == 'text_reference':
                from songs.text_reference import run
                context_path=args.output.parent/'reference_context.json'
                context=json.loads(context_path.read_text()) if context_path.is_file() else {}
                context['hypotheses']=list(result.get('hypotheses') or [])
                if args.baseline and args.baseline.is_file():
                    context['hypotheses'].append(json.loads(args.baseline.read_text()))
                selected=result.get('selected') or {}
                phrases=copy.deepcopy(context.get('current_phrases') or selected.get('phrases') or [])
                for phrase in phrases:
                    if phrase.get('phrase_id') in context.get('protected_phrase_ids',[]):phrase['manual_edited']=True
                def verify(phrase,item):
                    return verify_reference_candidate(args.input,phrase,item,result.get('hypotheses',[]),
                        lambda label:progress('Сверка с источником текста: '+label))
                selected['phrases'],result['text_reference']=run(phrases,context,verify=verify,progress=progress)
                result['selected']=selected
            elif step == 'chords':
                from songs.harmony import infer_chords
                result['harmony'] = infer_chords(args.input)
            elif step == 'structure':
                from songs.structure import infer_sections
                selected = result.get('selected') or {}
                selected['sections'] = infer_sections(selected.get('phrases') or [])
                result['selected'] = selected
            elif step == 'align':
                timed = result.get('selected')
                if timed:
                    context = list(result.get('hypotheses') or [])
                    if args.baseline and args.baseline.is_file():
                        context.insert(0, json.loads(args.baseline.read_text()))
                    initial_start = (timed.get('phrases') or [{}])[0].get('start_ms', 0)
                    generated = []
                    def vocals_for_timing():
                        path = _separate_vocals_for_intro(args.input,args.output,progress)
                        if path:
                            generated.append(path)
                        return path
                    try:
                        align_phrases(args.input,timed,progress,context,vocal_factory=vocals_for_timing)
                    finally:
                        # Only this job's temporary separator outputs, never a
                        # selected karaoke file or an existing cached stem.
                        import shutil
                        for path in generated:
                            shutil.rmtree(path.parent,ignore_errors=True)
                    align_intro_from_vocals(args.input,args.output,timed,context,progress,initial_start)
            elif step == 'consensus':
                from songs.consensus import consensus, choose_spine, recover_omissions
                hypotheses = list(result['hypotheses'])
                if args.baseline and args.baseline.is_file():
                    hypotheses.insert(0,json.loads(args.baseline.read_text()))
                base = choose_spine(hypotheses)
                if base:
                    def checkpoint(report):
                        result['comparison']=report
                        save(args.output,result)
                    repaired, comparison = repair_disagreements(args.input, base, hypotheses, progress,
                        previous=result.get('comparison'),checkpoint=checkpoint)
                    result['comparison'] = comparison
                    # Votes describe uncertainty; they cannot undo a fresh audio repair.
                    result['selected'] = {'model':'time_local_audio_consensus_v2', 'spine_model':base['model'], 'phrases':consensus(
                        recover_omissions(repaired, hypotheses),[h for h in hypotheses if h is not base],choose_majority=False)}
        except Exception as exc:
            result['errors'].append({'step': step, 'error': str(exc)[:1000]})
        save(args.output, result)
        gc.collect()
    result['completed'] = True
    save(args.output, result)
    progress('Разбор завершён; требуется проверка по записи')


if __name__ == '__main__':
    main()
