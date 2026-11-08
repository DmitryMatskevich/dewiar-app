"""Отдельный процесс разбиения по участникам: sherpa-onnx живёт только здесь, ядро его не импортирует.

Запуск: собранное приложение — «Dewiar --diarize-worker» (tools/macos_entry.py), из исходников —
«python -B core/diarization/worker.py». На stdin — задача JSON:
  {"wav", "segmentation", "embedding", "num_speakers": 0, "max_speakers": 0, "threshold": 0.5,
   "num_threads": 2, "min_duration_on": 0.3, "min_duration_off": 0.5}
На stdout — строки JSON: {"type": "progress", "value"} … {"type": "result", "turns": [...], "engine": {...}}
или {"type": "error", "message"}. Файл самодостаточен (stdlib + numpy + sherpa_onnx): работает и как скрипт.
"""
import json
import sys
import time
import wave

RATE = 16000


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read_wav(path):
    """WAV -> float32 моно 16 кГц. Записи встреч уже такие; иное — простое усреднение каналов и интерполяция."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError("ожидается WAV PCM 16 бит")
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != RATE and len(a):
        x = np.linspace(0, len(a) - 1, int(len(a) * RATE / sr))
        a = np.interp(x, np.arange(len(a)), a).astype(np.float32)
    return a


def _engine(task, num_clusters):
    try:
        import sherpa_onnx
    except ImportError as e:
        raise RuntimeError("не установлен модуль sherpa-onnx (requirements: sherpa-onnx>=2.0)") from e
    threads = max(1, int(task.get("num_threads") or 2))
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(task["segmentation"])),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(task["embedding"]), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=int(num_clusters or 0),
                                                    threshold=float(task.get("threshold") or 0.4)),
        min_duration_on=float(task.get("min_duration_on", 0.3)),
        min_duration_off=float(task.get("min_duration_off", 0.5)),
    )
    if not config.validate():
        raise RuntimeError("модели разбиения по участникам не найдены")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def diarize(task, samples, progress=None):
    """-> (интервалы [{start, end, speaker}], число говорящих, использованный num_clusters)."""
    def run(num_clusters, base, share):
        sd = _engine(task, num_clusters)
        if sd.sample_rate != RATE:
            raise RuntimeError(f"движок ждёт {sd.sample_rate} Гц")

        def cb(done, total):
            if progress:
                progress(base + share * done / max(total, 1))
            return 0                                     # отмена — завершением процесса
        segs = sd.process(samples, callback=cb).sort_by_start_time()
        return [{"start": round(float(s.start), 3), "end": round(float(s.end), 3), "speaker": f"S{int(s.speaker)}"}
                for s in segs]

    exact = int(task.get("num_speakers") or 0)
    cap = int(task.get("max_speakers") or 0)
    turns = run(exact, 0.0, 1.0 if exact or not cap else 0.6)
    used = exact
    if not exact and cap and len({t["speaker"] for t in turns}) > cap:
        turns, used = run(cap, 0.6, 0.4), cap            # больше говорящих, чем участников в списке встречи
    if progress:
        progress(1.0)
    return turns, len({t["speaker"] for t in turns}), used


def worker_main(argv=None):
    t0 = time.monotonic()
    try:
        task = json.loads(sys.stdin.read() or "{}")
        samples = read_wav(task["wav"])
        if len(samples) < RATE:
            emit({"type": "result", "turns": [], "engine": {"name": "sherpa-onnx"}, "elapsed": 0.0})
            return 0
        last = [0.0]

        def progress(p):
            if p - last[0] >= 0.02 or p >= 1.0:
                last[0] = p
                emit({"type": "progress", "value": round(min(1.0, p), 3)})
        turns, n, used = diarize(task, samples, progress)
        try:
            import sherpa_onnx
            version = getattr(sherpa_onnx, "__version__", "")
        except Exception:  # noqa: BLE001
            version = ""
        emit({"type": "result", "turns": turns, "speakers": n,
              "engine": {"name": "sherpa-onnx", "version": version, "threshold": float(task.get("threshold") or 0.4),
                         "num_clusters": used, "segmentation": "pyannote-segmentation-3.0",
                         "embedding": str(task.get("embedding", "")).replace("\\", "/").rsplit("/", 1)[-1]},
              "seconds": round(len(samples) / RATE, 1), "elapsed": round(time.monotonic() - t0, 2)})
        return 0
    except Exception as e:  # noqa: BLE001 — причина уходит в ядро одной строкой
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"[:500]})
        return 1


if __name__ == "__main__":
    sys.exit(worker_main(sys.argv[1:]))
