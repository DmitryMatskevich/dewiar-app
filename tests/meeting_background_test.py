"""Completed sessions process independently of capture and the next meeting. No devices/network."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from prompter import Prompter
from engine_brain import BrainMixin


class Settings(dict):
    def active_llm(self):
        return None


class BackgroundMeeting(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.events = []
        engine = SimpleNamespace(s=Settings(prompter={}), stopping=threading.Event(),
            workspace=SimpleNamespace(ensure=lambda: self.root),
            emit=lambda *a, **k: self.events.append(k), set_state=lambda *a: None)
        self.pr = Prompter(engine)
        self.pr.meeting = {"id": "first", "title": "First", "started": time.time() - 2}
        self.pr.on = True
        self.pr._transcript_open("First")
        self.pr._session_dir = Path(self.pr._transcript_path).with_suffix("")
        self.pr._session_dir.mkdir()
        self.pr._record_open("First")
        self.pr._record_write(b"\x10\x10" * 32000)
        self.pr._record_close()
        self.pr._frame_events = [{"path": "fixture.png", "audio_seconds": 0}]
        self.video_entered, self.text_entered = threading.Event(), threading.Event()
        self.video_release, self.text_release = threading.Event(), threading.Event()
        self.video_calls = 0

    def tearDown(self):
        self.video_release.set()
        self.text_release.set()
        if self.pr.on:
            self.pr._frame_events = []
            self.pr.stop(summary=False)
        if self.pr._post_thread:
            self.pr._post_thread.join(3)
        self.pr.eng.stopping.set()
        self.folder.cleanup()

    def video(self, events, audio, root, output, ffmpeg):
        self.video_calls += 1
        self.video_entered.set()
        if not self.video_release.wait(5):
            raise TimeoutError("test barrier")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"synthetic mp4; real codec covered by meeting_video_test")
        return str(output)

    def summary(self, *args):
        self.text_entered.set()
        if not self.text_release.wait(5):
            raise TimeoutError("test barrier")
        return "## Договорились\n- Fixture decision"

    def launch(self):
        started = time.monotonic()
        self.pr.stop()
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(self.video_entered.wait(2), "video must start without a manual export")
        self.assertTrue(self.text_entered.wait(2), "summary must not wait for video")
        self.assertTrue(self.pr.public()["can_start"])
        self.assertTrue(self.pr.public()["processing"])
        return self.pr._completed_job, self.pr._post_thread

    def test_summary_first_then_video_updates_saved_file(self):
        with patch("meeting_video.build_meeting_video", self.video), patch.object(Prompter, "_summary_text", self.summary):
            job, thread = self.launch()
            self.text_release.set()
            deadline = time.monotonic() + 2
            while not job._summary_path and time.monotonic() < deadline:
                time.sleep(0.01)
            path = Path(job._summary_path)
            self.assertIn('## Итоги\n', path.read_text())
            self.assertNotIn('## Summary\n', path.read_text())
            self.assertIn("создаётся в фоне", path.read_text())
            path.write_text(path.read_text() + "\nUser edit\n")
            self.video_release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            body = path.read_text()
            self.assertIn("Видео: готово", body)
            self.assertIn("User edit", body)
            self.assertEqual(body.count("Открыть видео встречи"), 1)
            self.assertFalse(self.pr.public()["processing"])
            self.assertTrue(self.pr.public()["recording"]["video_file"].endswith(".mp4"))
            self.assertFalse(self.pr.export_meeting_video()["busy"])
            self.assertEqual(self.video_calls, 1, "opening a finished video must not rebuild it")

    def test_video_first_and_new_meeting_is_isolated(self):
        with patch("meeting_video.build_meeting_video", self.video), patch.object(Prompter, "_summary_text", self.summary), patch.object(Prompter, "_preload_stt"), patch.object(Prompter, "_probe_record_source"), patch.object(Prompter, "_them_loop", lambda pr, stop, q, session: pr._capture_done.set()):
            job, thread = self.launch()
            self.video_release.set()
            job._video_thread.join(2)
            state = self.pr.public()
            self.assertTrue(state["summary_busy"])
            self.assertTrue(state["recording"]["video_file"])
            self.pr.start(title="Second", them=True, me=False, record=True, deferred=True,
                          consent=True, auto_first_frame=False)
            second = self.pr.meeting["id"]
            self.assertNotEqual(second, "first")
            second_audio = Path(self.pr._record_path)
            self.assertNotEqual(second_audio, Path(job._record_path))
            self.pr._record_write(b"\x11\x11" * 32000)
            self.text_release.set()
            thread.join(3)
            self.assertEqual(self.pr.meeting["id"], second)
            self.assertEqual(self.pr.summary, "")
            self.assertEqual(self.pr._video_path, "")
            self.assertTrue(self.pr.public()["on"])
            self.assertTrue(second_audio.is_file())
            self.assertTrue(Path(job._record_path).is_file())
            self.assertIn("Видео: готово", Path(job._summary_path).read_text())
            summaries = [e for e in self.events if e.get("kind") == "summary" and not e.get("busy")]
            self.assertTrue(summaries)
            self.assertTrue(all(e["meeting_id"] == "first" for e in summaries))

    def test_video_failure_preserves_summary_and_can_retry(self):
        self.text_release.set()
        self.video_release.set()
        with patch("meeting_video.build_meeting_video", side_effect=RuntimeError("fixture encoder error")), patch.object(Prompter, "_summary_text", self.summary):
            self.pr.stop()
            self.pr._post_thread.join(3)
        job = self.pr._completed_job
        self.assertIn("Fixture decision", job.summary)
        self.assertIn("fixture encoder error", job.summary)
        self.assertFalse(self.pr.public()["processing"])
        with patch("meeting_video.build_meeting_video", self.video):
            self.pr.export_meeting_video()
            job._video_thread.join(3)
        self.assertIn("Видео: готово", Path(job._summary_path).read_text())
        self.assertNotIn("fixture encoder error", job.summary)

    def test_no_frames_still_produces_summary(self):
        self.pr._frame_events = []
        self.text_release.set()
        with patch("meeting_video.build_meeting_video") as build, patch.object(Prompter, "_summary_text", self.summary):
            self.pr.stop()
            self.pr._post_thread.join(3)
            build.assert_not_called()
        self.assertIn("нет сохранённых кадров", self.pr.summary)
        self.assertFalse(self.pr.public()["processing"])

    def test_delete_targets_only_completed_meeting_and_respects_processing(self):
        job = self.pr._completed_snapshot()
        self.pr.on = False
        self.pr._completed_job = job
        root = self.root / 'Встречи'
        result = root / (job._base_name + '.md')
        result.write_text('# Тест\n\n## Итоги\n')
        job._summary_path, job.summary = str(result), result.read_text()
        video = job._session_dir / 'Видео' / 'Видео встречи.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(b'synthetic')
        job._video_path = str(video)
        unrelated = root / 'Аудио' / 'другая встреча.wav'
        unrelated.write_bytes(b'not mine')
        with patch('guard.plan_delete', side_effect=lambda paths: paths) as plan, patch('guard.trash') as trash:
            job._summary_busy = True
            with self.assertRaises(ValueError): self.pr.remove_completed('first')
            job._summary_busy = False
            with self.assertRaises(ValueError): self.pr.remove_completed('wrong')
            self.pr.remove_completed('first', video_only=True)
            self.assertEqual(plan.call_args.args[0], [video.resolve()])
            self.assertEqual(job._video_path, '')
            self.assertIn('удалено пользователем', job.summary)
            self.assertTrue(unrelated.exists())
            job._video_path = str(video)
            self.pr.remove_completed('first')
            paths = plan.call_args.args[0]
            self.assertIn(job._session_dir.resolve(), paths)
            self.assertNotIn(video.resolve(), paths)  # parent directory is trashed once
            self.assertNotIn(unrelated.resolve(), paths)
            self.assertEqual(trash.call_count, 2)
            self.assertIsNone(self.pr._completed_job)

    def test_transcription_boundaries_use_bounded_arrays(self):
        import numpy as np
        pcm = np.full(16000 * 60, 1200, dtype=np.int16)
        pcm[16000 * 20:16000 * 21] = 0
        progress = []
        eng = SimpleNamespace(stopping=threading.Event())
        stt = SimpleNamespace(transcribe=lambda data: "phrase")
        original_arange = np.arange
        lengths = []
        def arange(*args, **kw):
            result = original_arange(*args, **kw)
            lengths.append(len(result))
            return result
        with patch("engine_brain.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=pcm.tobytes())), patch("eyes.ffmpeg_path", return_value="fixture"), patch.object(np, "arange", arange):
            lines = BrainMixin.transcribe_file(eng, "fixture.wav", stt=stt, on_progress=lambda done, total: progress.append(done / total))
        self.assertGreaterEqual(len(lines), 3)
        self.assertTrue(20 <= lines[0][1] <= 21)
        self.assertEqual(lines[-1][1], 60)
        self.assertEqual(progress[-1], 1)
        self.assertLessEqual(max(lengths), 16000 * 6)


if __name__ == "__main__":
    unittest.main()
