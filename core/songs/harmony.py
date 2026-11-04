"""Предварительные аккорды: локальный Chordino, резерв — CQT/Viterbi.

Зависимости загружаются только музыкальным worker. Наличие результата
не означает проверенный гитарный подбор; прежние варианты сохраняются.
"""
import numpy as np
import subprocess

NAMES = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B')
QUALITIES = {'': (0, 4, 7), 'm': (0, 3, 7), '7': (0, 4, 7, 10),
             'm7': (0, 3, 7, 10), 'maj7': (0, 4, 7, 11), 'dim': (0, 3, 6),
             'sus4': (0, 5, 7)}


def templates():
    names, vectors = [], []
    for root, name in enumerate(NAMES):
        for quality, intervals in QUALITIES.items():
            vector = np.zeros(12)
            for interval in intervals:
                vector[(root+interval) % 12] = 1 if interval else 1.15
            vectors.append(vector / np.linalg.norm(vector))
            names.append(name+quality)
    return names, np.array(vectors)


def infer_chords(path):
    from .chordino import binary_path, infer
    if binary_path():
        try:
            return infer(path)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            fallback = infer_chroma_chords(path)
            fallback['fallback_reason'] = str(exc)
            return fallback
    return infer_chroma_chords(path)


def infer_chroma_chords(path):
    import librosa
    from scipy.ndimage import median_filter
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    if len(y) < sr:
        return {'events': [], 'method': 'chroma_viterbi_v1', 'duration_ms': round(len(y)/sr*1000)}
    harmonic = librosa.effects.harmonic(y)
    hop = 512
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, hop_length=hop)
    chroma = median_filter(chroma, size=(1, 9))
    # Fixed 0.23 s cells avoid imposing a wrong beat grid on rubato singing.
    stride = 10
    cells = np.stack([np.mean(chroma[:, i:i+stride], axis=1)
                      for i in range(0, chroma.shape[1], stride)], axis=1)
    cells /= np.maximum(np.linalg.norm(cells, axis=0), 1e-8)
    names, bank = templates()
    scores = bank @ cells
    # Extensions require additional evidence; otherwise triads win ties.
    penalty = np.array([.035 if any(ch.isdigit() for ch in name) else 0 for name in names])
    scores -= penalty[:, None]
    size, frames = scores.shape
    previous = scores[:, 0].copy()
    back = np.zeros((frames, size), dtype=np.int16)
    for t in range(1, frames):
        best = int(np.argmax(previous))
        stay, change = previous, previous[best] - .20
        choose_stay = stay >= change
        back[t] = np.where(choose_stay, np.arange(size), best)
        previous = scores[:, t] + np.maximum(stay, change)
    path_ids = np.zeros(frames, dtype=int)
    path_ids[-1] = np.argmax(previous)
    for t in range(frames-1, 0, -1):
        path_ids[t-1] = back[t, path_ids[t]]
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    energy = np.array([np.mean(rms[i:i+stride]) for i in range(0, len(rms), stride)])
    threshold = max(float(np.max(energy)) * .015, .0003)
    events, active = [], None
    cell_ms = stride * hop / sr * 1000
    for t, label in enumerate(path_ids):
        name = names[label] if t < len(energy) and energy[t] >= threshold else 'N'
        if name != active:
            if events:
                events[-1]['end_ms'] = round(t*cell_ms)
            events.append({'name': name, 'start_ms': round(t*cell_ms),
                           'end_ms': round(len(y)/sr*1000), 'method': 'chroma_viterbi_v1',
                           'confidence': round(float(scores[label, t]), 3), 'verified': False})
            active = name
    return {'events': events, 'method': 'chroma_viterbi_v1', 'vocabulary': 'triads_7_sus4',
            'review_required': True, 'duration_ms': round(len(y)/sr*1000),
            'notation': 'B=си, Bb=си-бемоль', 'resolution_ms': round(cell_ms)}
