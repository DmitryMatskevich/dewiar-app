"""Минимальные проверки фонового расписания записи встреч."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from prompter import Prompter  # noqa: E402


class FakeSettings(dict):
    def get(self, key, *rest):
        value = super().get(key, None)
        return value if value is not None else (rest[0] if rest else None)


class FakeEngine:
    def __init__(self):
        self.s = FakeSettings(prompter={})
        self.stopping = threading.Event()
        self.events = []

    def emit(self, *args, **kwargs):
        self.events.append((args, kwargs))


eng = FakeEngine()
pr = Prompter(eng)
with tempfile.TemporaryDirectory() as td:
    pr._schedules_path = Path(td) / "schedules.json"
    pr._schedules = []
    start = time.time() + 60
    item = pr.schedule(start, start + 600, "Демо", {"consent": True, "record": True})
    assert item["state"] == "scheduled"
    assert pr.schedules()[0]["title"] == "Демо"
    assert pr.cancel_schedule(item["id"])["cancelled"] == item["id"]
    try:
        pr.schedule(start, start + 10, "Без согласия", {})
    except ValueError as exc:
        assert "согласие" in str(exc)
    else:
        raise AssertionError("запись без согласия должна быть отклонена")

eng.stopping.set()
pr._schedule_thread.join(timeout=1)
print("OK: расписание записи, отмена и проверка согласия")
