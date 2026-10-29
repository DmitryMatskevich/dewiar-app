"""Explicit Mac model downloads in a dedicated process, without opening network access for the core.

The worker accepts only known GigaAM models and downloads public weights without implicit credentials.
Reports use a file because a windowed frozen .app has no reliable stdout.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from settings import MODELS_DIR, STATE_DIR


def download_gigaam(name, timeout=900):
    directory = STATE_DIR / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gigaam-", dir=directory) as tmp:
        report = Path(tmp) / "result.json"
        command = ([sys.executable, "--download-gigaam"] if getattr(sys, "frozen", False)
                   else [sys.executable, str(Path(__file__).resolve())])
        run = subprocess.run(command + [name, str(report)], capture_output=True, timeout=timeout)
        result = json.loads(report.read_text()) if report.exists() else {}
        if run.returncode or not result.get("ok"):
            raise RuntimeError(result.get("error") or "Процесс загрузки GigaAM завершился без результата")
        return result


def worker_main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("report", type=Path)
    parser.add_argument("--wav", type=Path, help="Optional synthetic 16 kHz mono PCM fixture for acceptance")
    args = parser.parse_args(argv)
    staging = backup = None
    result = {"ok": False, "model": args.model}
    try:
        os.environ["HF_HOME"] = str(MODELS_DIR / "hf")
        os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
        for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            os.environ.pop(key, None)
        import truststore
        truststore.inject_into_ssl()
        import numpy as np
        import onnx_asr
        import onnxruntime as rt
        from onnx_asr.resolver import model_repos
        if args.model not in model_repos or not args.model.startswith("gigaam-"):
            raise ValueError("Загрузка доступна только для известных моделей GigaAM")
        options = rt.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        target = MODELS_DIR / args.model
        target.parent.mkdir(parents=True, exist_ok=True)
        model = None
        if target.is_dir():
            try:
                model = onnx_asr.load_model(args.model, str(target), quantization="int8", sess_options=options, providers=["CPUExecutionProvider"])
            except Exception:
                pass  # An incomplete prior download must not be reported as installed.
        if model is None:
            staging = target.with_name(target.name + ".download-" + uuid.uuid4().hex)
            model = onnx_asr.load_model(args.model, str(staging), quantization="int8", sess_options=options, providers=["CPUExecutionProvider"])
        model.recognize(np.zeros(8000, dtype=np.float32))
        if args.wav:
            import wave
            with wave.open(str(args.wav), "rb") as audio:
                if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                    raise ValueError("Для проверки нужен WAV 16000 Hz, mono, PCM16")
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            result["transcript"] = model.recognize(samples)
        del model
        if staging:
            if target.exists():
                backup = target.with_name(target.name + ".previous-" + uuid.uuid4().hex)
                target.replace(backup)
            try:
                staging.replace(target)
                staging = None
            except Exception:
                if backup:
                    backup.replace(target)
                    backup = None
                raise
        result.update(ok=True, path=str(target), onnx_asr_version=onnx_asr.__version__, warmup=True)
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
        if backup and result["ok"]:
            shutil.rmtree(backup, ignore_errors=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(worker_main())
