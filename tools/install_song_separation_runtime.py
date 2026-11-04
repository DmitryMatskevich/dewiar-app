"""Install the already provisioned, offline HTDemucs runtime for the Mac app."""
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".songs-separation-venv"
DESTINATION = Path.home() / "Library/Application Support/Dewiar/runtimes/separation"


def main():
    source_python = SOURCE / "bin/python"
    if not source_python.is_file():
        raise SystemExit("Локальная .songs-separation-venv не найдена")
    check = subprocess.run([str(source_python), "-c", "import torch, torchaudio, demucs"],
                           capture_output=True, text=True)
    if check.returncode:
        raise SystemExit("Среда HTDemucs не прошла проверку импортов: " + check.stderr[-1500:])
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    if (DESTINATION / "bin/python").is_file():
        print("Runtime минусовок уже установлен:", DESTINATION)
        return
    staging = DESTINATION.with_name(DESTINATION.name + ".installing")
    if staging.exists():
        raise SystemExit("Найдена незавершённая установка runtime: " + str(staging))
    shutil.copytree(SOURCE, staging, symlinks=True)
    copied_python = staging / "bin/python"
    check = subprocess.run([str(copied_python), "-c", "import torch, torchaudio, demucs"],
                           capture_output=True, text=True)
    if check.returncode:
        raise SystemExit("Скопированный runtime не прошёл проверку: " + check.stderr[-1500:])
    staging.rename(DESTINATION)
    print("Runtime минусовок установлен офлайн:", DESTINATION)


if __name__ == "__main__":
    main()
