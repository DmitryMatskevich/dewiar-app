"""Per-stage song retries keep the completed parts of a song intact."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from songs.analysis import SongAnalyzer
from songs.store import SongStore


class SongStepRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SongStore(Path(self.tmp.name) / "songs")
        self.row = self.store.create("Питер в твоих волосах")
        self.sid = self.row["song_id"]
        self.audio = self.store.root / self.sid / "audio" / "song.wav"
        self.audio.parent.mkdir(parents=True)
        self.audio.write_bytes(b"RIFF")
        self.store.patch(self.sid, {"audio": {"path": str(self.audio.relative_to(self.store.root))},
                                    "capabilities": {"audio": True}})
        self.phrase = {"phrase_id": "run-0", "text": "Питер в твоих волосах", "start_ms": 0,
                       "end_ms": 5000, "words": [], "timing_quality": "phrase",
                       "review_state": "needs_review"}
        self.store.patch(self.sid, {"status": "running", "active_run_id": "run-1"})
        saved = self.store.save_full_analysis(self.sid, "run-1", [self.phrase], "gigaam", {
            "hypotheses": [{"model": "Whisper-test", "phrases": [self.phrase]}],
            "harmony": {"events": [{"name": "Am", "start_ms": 0}]},
        })
        self.assertIsNotNone(saved)
        folder = self.audio.parent.parent / "analysis" / "run-1"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "result.json").write_text(json.dumps({
            "hypotheses": [{"model": "Whisper-test", "phrases": [self.phrase]}],
            "selected": {"phrases": [self.phrase]},
            "harmony": {"events": [{"name": "Am", "start_ms": 0}]},
            "errors": [], "completed": True,
        }), encoding="utf-8")
        self.events = []
        self.done = threading.Event()
        def emit(event, **payload):
            self.events.append((event, payload))
            if not payload.get("running"):
                self.done.set()
        self.analyzer = SongAnalyzer(self.store.root, self.store, Mock(), emit)

    def test_chords_retry_updates_only_chords(self):
        def fake_analyze(path, output, run_id, cancel, progress, steps, resume):
            self.assertEqual(steps, "chords")
            self.assertTrue(resume)
            return {"hypotheses": [{"model": "Whisper-test", "phrases": [self.phrase]}],
                    "selected": {"phrases": [self.phrase]},
                    "harmony": {"events": [{"name": "D7", "start_ms": 1200}]}, "errors": []}

        with patch("songs.runtime.analyze", side_effect=fake_analyze):
            started = self.analyzer.rerun_step(self.sid, "chords")
            self.assertEqual(started["status"], "running")
            self.assertTrue(self.done.wait(3))
        row = self.store.get(self.sid)
        self.assertEqual([event["name"] for event in row["chords"]], ["D7"])
        self.assertEqual(row["phrases"][0]["text"], "Питер в твоих волосах")
        self.assertIsNone(row["active_run_id"])
        self.assertIn("Проверка аккордов завершена", row["stage_label"])

    def test_text_source_retry_uses_current_text_and_keeps_chords(self):
        def fake_analyze(path, output, run_id, cancel, progress, steps, resume):
            self.assertEqual(steps,"text_reference,align,structure")
            context=json.loads((output.parent/'reference_context.json').read_text())
            self.assertEqual(context['current_phrases'][0]['text'],self.phrase['text'])
            progress('Сверка с источником текста: ищем публикации')
            self.assertEqual(self.store.get(self.sid)['stage'],'text_reference')
            return {'selected':{'phrases':[self.phrase]},'text_reference':{'state':'not_found','references':[]},'errors':[]}
        before=self.store.get(self.sid)
        with patch('songs.runtime.analyze',side_effect=fake_analyze):
            self.analyzer.rerun_step(self.sid,'text_reference')
            self.assertTrue(self.done.wait(3))
        row=self.store.get(self.sid)
        self.assertEqual(row['text_reference']['state'],'not_found')
        self.assertEqual(row['chords'],before['chords'])
        self.assertEqual(row['audio'],before['audio'])
        self.assertEqual(row['phrases'][0]['text'],self.phrase['text'])


if __name__ == "__main__":
    unittest.main()
