"""Independent song workers must have more than one heavy-processing slot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from songs.analysis import SongAnalyzer
from songs.instrumental import InstrumentalManager


def check_slots(owner, label):
    slots = owner._heavy_slots
    assert slots.acquire(timeout=0.2), f"{label}: first slot unavailable"
    try:
        assert slots.acquire(timeout=0.2), f"{label}: second independent job was serialized"
        slots.release()
    finally:
        slots.release()


check_slots(SongAnalyzer, "распознавание")
check_slots(InstrumentalManager, "минусовка")
print("OK: распознавание и минусовка допускают параллельные независимые задачи")
