"""Единая точка входа .app: интерфейс либо отдельный процесс ядра.

Замороженный sys.executable — это приложение, поэтому ядро выбирается флагом --core.
"""
import runpy
import sys
import multiprocessing
from pathlib import Path

multiprocessing.freeze_support()  # Hugging Face/tqdm starts a resource-tracker subprocess in frozen builds.

root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "core"), str(root / "ui")]
if "--download-gigaam" in sys.argv:
    from model_download import worker_main
    raise SystemExit(worker_main(sys.argv[sys.argv.index("--download-gigaam") + 1:]))
if "--tts-worker" in sys.argv:
    sys.argv = [str(root / "core/tts_worker.py")] + sys.argv[sys.argv.index("--tts-worker") + 1:]
    runpy.run_path(str(root / "core/tts_worker.py"), run_name="__main__")
    raise SystemExit(0)
core = "--core" in sys.argv
if core:
    sys.argv.remove("--core")
runpy.run_path(str(root / ("core/main.py" if core else "ui/app.py")), run_name="__main__")
