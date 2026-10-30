"""Ссылки из итога встречи ведут только к её локальным файлам."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from prompter import Prompter  # noqa: E402


class FakeEngine:
    def __init__(self, root):
        self.workspace = SimpleNamespace(ensure=lambda: root)
        self.s = SimpleNamespace(active_llm=lambda: None, get=lambda key: {})
        self.events = []

    def emit(self, *args, **kwargs):
        self.events.append((args, kwargs))


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    meeting = root / "Встречи"
    audio = meeting / "Аудио" / "запись.wav"
    transcript = meeting / "Расшифровки" / "текст.txt"
    frame = meeting / "Расшифровки" / "Кадры" / "0001.jpg"
    video = meeting / "Видео" / "встреча.mp4"
    for path in (audio, transcript, frame, video):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"file")

    p = Prompter(FakeEngine(root))
    p.meeting = {"title": "Склад", "goal": "обсудить", "started": 1, "keep_text": True}
    p.lines = [{"who": "them", "text": "Дмитрий, всё готово", "t": 60}]
    p._record_path = str(audio)
    p._transcript_path = str(transcript)
    p._screenshots = [{"path": str(frame), "ocr": "кадр"}]
    p._video_path = str(video)
    p._ask_model = lambda *args, **kwargs: "## Договорились\n- готово"
    p._summarize()

    assert "- Участники: Дмитрий" in p.summary
    assert p.public()["meeting"]["participants"] == "Дмитрий"
    assert "[Аудио/запись.wav](dwr-file:" in p.summary
    assert "[Расшифровки/текст.txt](dwr-file:" in p.summary
    assert "[Расшифровки/Кадры/0001.jpg](dwr-file:" in p.summary
    assert "[встреча.mp4](dwr-file:" in p.summary
    assert Path(p._summary_path).is_file()
    assert p.public()["summary_path"] == p._summary_path

print("OK: аудио, расшифровка, кадр и видео в итоговом протоколе — ссылки")
