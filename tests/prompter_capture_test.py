"""Проверка локального конвейера кадра: PNG, OCR-маркер, таймкод и живой .txt."""
import sys
import tempfile
import threading
import io
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from prompter import Prompter  # noqa: E402
import ocr  # noqa: E402


class Engine:
    def __init__(self, root):
        self.events = []
        self.workspace = SimpleNamespace(ensure=lambda: root)
        self.s = SimpleNamespace(active_llm=lambda: None)
        self.audio_suspended = threading.Event()
        self.restart_audio = threading.Event()

    def emit(self, type_, **kw):
        self.events.append((type_, kw))

    def set_state(self, *_):
        pass


root = Path(tempfile.mkdtemp())
eng = Engine(root)
pr = Prompter(eng)
try:
    try:
        pr.start(title="Запись", record=True)
        raise AssertionError("запись запустилась без согласия")
    except ValueError as exc:
        assert "соглас" in str(exc)

    pr.start(title="Запись", keep_text=True, them=False, me=False)
    old = ocr.recognize
    ocr.recognize = lambda data: "Слайд: решение принято"
    try:
        one = pr.capture_frame(b"fake-png")
        two = pr.capture_frame(b"fake-png")
    finally:
        ocr.recognize = old
    assert one == two
    assert one["ocr"] == "Слайд: решение принято"
    assert len(pr._screenshots) == 1
    pr.add_line("them", "Обсудили сроки")
    pr.stop(summary=False)
    import time
    deadline = time.monotonic() + 5
    while pr._post_busy and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not pr._post_busy, "остановка захвата должна завершиться перед следующей встречей"
    txt = Path(pr._transcript_path)
    body = txt.read_text(encoding="utf-8")
    assert "Экран: Слайд: решение принято" in body
    assert "Собеседник: Обсудили сроки" in body
    assert Path(root / "Встречи" / "Расшифровки").is_dir()
    assert Path(txt.parent / one["path"]).is_file()

    # Интервал и качество ограничиваются безопасным диапазоном; автокадр хранится JPEG.
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 18), "white").save(buf, "PNG")
    pr.start(title="Кадры", auto_interval=0.1, frame_quality=120, them=False, me=False)
    assert pr._frame_interval == 0.5 and pr._frame_quality == 100
    auto = pr.capture_frame(buf.getvalue(), auto=True)
    assert auto["path"].endswith(".jpg") and (Path(pr._transcript_path).parent / auto["path"]).is_file()
    pr.stop(summary=False)
    deadline = time.monotonic() + 5
    while pr._post_busy and time.monotonic() < deadline:
        time.sleep(0.02)
finally:
    import shutil
    shutil.rmtree(root, ignore_errors=True)

print("OK: снимок экрана, локальный OCR, дедупликация и живой transcript.txt")
