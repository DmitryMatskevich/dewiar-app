"""Reuse existing local Tesseract RU/EN models; no network or old-project runtime dependency."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from settings import MODELS_DIR


def install(source, destination):
    source, destination = Path(source), Path(destination)
    assets = [source / (lang + ".traineddata") for lang in ("rus", "eng")]
    if not all(path.is_file() and path.stat().st_size > 1024 for path in assets):
        raise ValueError("В исходной папке нужны rus.traineddata и eng.traineddata")
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for src in assets:
        target = destination / src.name
        if src.resolve() != target.resolve():
            temp = target.with_suffix(".tmp")
            shutil.copyfile(src, temp)  # Follow old project's symlink, create independent asset.
            temp.replace(target)
        records.append({"name": src.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    (destination / "manifest.json").write_text(json.dumps({"source": str(source.resolve()), "files": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=MODELS_DIR / "ocr/tessdata")
    args = parser.parse_args()
    print(install(args.source, args.destination))
