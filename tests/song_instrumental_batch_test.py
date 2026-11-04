"""Batch karaoke preparation queues songs with owned audio and skips ready/running cards."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from engine_commands import CommandsMixin


class BatchManager:
    def __init__(self, root):
        self.root = root
        self.starts = []

    def summary(self, song_id):
        return {"state": "ready", "artifact": {"name": "backing.wav"}} if song_id == "ready-song" else {"state": "absent"}

    def running(self, song_id):
        return song_id == "running-song"

    def start(self, song_id):
        self.starts.append(song_id)
        if song_id == "bad-song":
            raise RuntimeError("fixture failure")
        return {"reused": False}


class SongInstrumentalBatchTest(unittest.TestCase):
    def test_batch_skips_ready_running_and_missing_audio_but_continues_after_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for sid in ("ready-song", "running-song", "missing-song", "queued-song", "bad-song"):
                path = root / sid / "audio.wav"
                if sid != "missing-song":
                    path.parent.mkdir(parents=True)
                    path.write_bytes(b"synthetic")
                rows.append({"song_id": sid, "title": sid, "audio": {"path": str(path.relative_to(root))}})
            manager = BatchManager(root)
            engine = SimpleNamespace(song_store=SimpleNamespace(root=root, list=lambda limit: rows), song_instrumental=manager)
            result = CommandsMixin._command(engine, "songs_instrumental_prepare_all", {})
            self.assertEqual([x["song_id"] for x in result["ready"]], ["ready-song"])
            self.assertEqual([x["song_id"] for x in result["already_running"]], ["running-song"])
            self.assertEqual([x["song_id"] for x in result["missing_audio"]], ["missing-song"])
            self.assertEqual([x["song_id"] for x in result["started"]], ["queued-song"])
            self.assertEqual([x["song_id"] for x in result["errors"]], ["bad-song"])
            self.assertEqual(manager.starts, ["queued-song", "bad-song"])


if __name__ == "__main__":
    unittest.main()
