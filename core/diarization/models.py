"""Модели разбиения по участникам. Без токенов: релизы sherpa-onnx на GitHub или своё зеркало (latest.json →
models.diarization_seg), как у модели голоса и GigaAM.

  • сегментация — pyannote segmentation-3.0 в ONNX (MIT), архив ~7 МБ, модель ~6 МБ;
  • голос — CAM++ из speaker.py (тот же файл и тот же SHA256, что у профиля владельца): второй раз не качается.
"""
import hashlib
import shutil
import tarfile
from pathlib import Path

SEG_NAME = "sherpa-onnx-pyannote-segmentation-3-0"
SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
           f"{SEG_NAME}.tar.bz2")
SEG_ARCHIVE_SHA256 = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
SEG_SHA256 = "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
KEEP = ("model.onnx", "LICENSE", "README.md")


def _models_dir():
    from settings import MODELS_DIR          # поздно: тесты задают DEWIAR_STATE_DIR до первого вызова
    return Path(MODELS_DIR)


def seg_dir():
    return _models_dir() / "diarization" / SEG_NAME


def seg_model():
    return seg_dir() / "model.onnx"


def embedding_model():
    from speaker import MODEL_NAME
    return _models_dir() / "speaker" / MODEL_NAME


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ready():
    return seg_model().is_file() and embedding_model().is_file()


def status():
    return {"segmentation": seg_model().is_file(), "embedding": embedding_model().is_file(), "ready": ready(),
            "mb": 7 if not seg_model().is_file() else 0}


def _extract(archive, dest):
    """Только нужные файлы, без путей вне папки (архив чужой — проверяем имена)."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as tar:
        for m in tar.getmembers():
            name = Path(m.name).name
            if not m.isfile() or name not in KEEP or ".." in Path(m.name).parts:
                continue
            src = tar.extractfile(m)
            if src is None:
                continue
            tmp = dest / (name + ".part")
            with open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            tmp.replace(dest / name)


def ensure(mirror=None, speaker=None, on_progress=None, download=None):
    """Скачать недостающее (сегментация; голос — через speaker.ensure). -> (сегментация, голос).
    mirror — {"url", "sha256"} архива на своём сайте; download — подмена загрузчика в тестах."""
    seg = seg_model()
    if not seg.is_file():
        if download is None:
            from updater import download
        archive = seg_dir().parent / f"{SEG_NAME}.tar.bz2"
        sources = ([(mirror["url"], mirror.get("sha256") or SEG_ARCHIVE_SHA256)] if (mirror or {}).get("url") else [])
        sources.append((SEG_URL, SEG_ARCHIVE_SHA256))
        err = None
        for url, sha in sources:
            try:
                download(url, archive, sha, on_progress)
                _extract(archive, seg_dir())
                if _sha(seg) != SEG_SHA256:
                    seg.unlink(missing_ok=True)
                    raise RuntimeError("контрольная сумма модели сегментации не совпала")
                err = None
                break
            except Exception as e:  # noqa: BLE001 — пробуем следующий источник
                err = e
            finally:
                archive.unlink(missing_ok=True)
        if err is not None or not seg.is_file():
            raise RuntimeError(f"модель разбиения по участникам не скачалась: {err}")
    emb = embedding_model()
    if not emb.is_file():
        if speaker is None:
            from speaker import Speaker
            speaker = Speaker()
        speaker.ensure(on_progress)
    return seg, emb
