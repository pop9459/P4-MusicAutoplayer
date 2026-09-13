"""Tests for src/bpm_analyzer.py.

aubio is an optional dependency that may not be installed, so every test
here patches `detect_bpm` or `_load_aubio` rather than touching real audio.
"""
from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.bpm_analyzer import (
    BpmAnalyzerUnavailableError,
    analyze_tracks,
    detect_bpm,
    start_bpm_task,
    tracks_needing_bpm,
)
from src.json_io import save_json
from src.track_analyzer import TrackRecord


def _track(track_id: str, bpm: float | None = None) -> TrackRecord:
    return TrackRecord(id=track_id, path=f"/{track_id}.mp3", title=track_id, artist="A", bpm=bpm)


class MissingDependencyTests(unittest.TestCase):
    def test_detect_bpm_explains_how_to_install_aubio(self) -> None:
        with patch("src.bpm_analyzer._load_aubio", side_effect=BpmAnalyzerUnavailableError("nope")):
            with self.assertRaises(BpmAnalyzerUnavailableError):
                detect_bpm("/whatever.mp3")

    def test_importing_the_module_does_not_require_aubio(self) -> None:
        # The import is deliberately lazy so the suite and `play` run
        # without the optional dependency installed.
        import src.bpm_analyzer as module

        self.assertNotIn("aubio", vars(module))


class PlausibilityTests(unittest.TestCase):
    def _detect_with_estimate(self, estimate: float) -> float | None:
        class FakeSource:
            samplerate = 44100

            def __init__(self, *_args: object) -> None:
                self._done = False

            def __call__(self) -> tuple[list[float], int]:
                if self._done:
                    return [], 0
                self._done = True
                return [0.0], 0

        class FakeTempo:
            def __init__(self, *_args: object) -> None:
                pass

            def __call__(self, _samples: object) -> None:
                pass

            def get_bpm(self) -> float:
                return estimate

        fake_aubio = type("FakeAubio", (), {"source": FakeSource, "tempo": FakeTempo})
        with patch("src.bpm_analyzer._load_aubio", return_value=fake_aubio):
            return detect_bpm("/track.mp3")

    def test_plausible_tempo_is_stored_raw(self) -> None:
        # Stored as detected: half/double-time equivalence is handled by the
        # similarity function, not by folding the value into a range.
        self.assertEqual(self._detect_with_estimate(174.0), 174.0)

    def test_implausible_tempo_is_rejected(self) -> None:
        self.assertIsNone(self._detect_with_estimate(3.0))
        self.assertIsNone(self._detect_with_estimate(900.0))


class ResumeTests(unittest.TestCase):
    def test_only_tracks_without_a_bpm_are_queued(self) -> None:
        tracks = [_track("a", bpm=128.0), _track("b"), _track("c", bpm=90.0), _track("d")]
        self.assertEqual([t.id for t in tracks_needing_bpm(tracks)], ["b", "d"])


class AnalyzeTracksTests(unittest.TestCase):
    def test_results_are_written_onto_the_tracks(self) -> None:
        tracks = [_track("a"), _track("b")]
        with patch("src.bpm_analyzer.detect_bpm", return_value=120.0):
            analyzed, failed = analyze_tracks(tracks)

        self.assertEqual(analyzed, 2)
        self.assertEqual(failed, [])
        self.assertEqual([t.bpm for t in tracks], [120.0, 120.0])

    def test_an_undetectable_file_is_reported_without_aborting_the_run(self) -> None:
        tracks = [_track("a"), _track("bad"), _track("c")]

        def _detect(path: str) -> float | None:
            return None if "bad" in str(path) else 120.0

        with patch("src.bpm_analyzer.detect_bpm", side_effect=_detect):
            analyzed, failed = analyze_tracks(tracks)

        self.assertEqual(analyzed, 2)
        self.assertEqual([t.id for t in failed], ["bad"])
        self.assertIsNone(next(t for t in tracks if t.id == "bad").bpm)

    def test_progress_is_checkpointed_during_the_run_not_only_at_the_end(self) -> None:
        # A run over thousands of files is expected to be interrupted, so
        # losing everything on Ctrl-C would make the command not worth using.
        tracks = [_track(str(i)) for i in range(10)]
        checkpoints: list[int] = []

        with patch("src.bpm_analyzer.detect_bpm", return_value=120.0):
            analyze_tracks(
                tracks,
                checkpoint_every=3,
                on_checkpoint=lambda: checkpoints.append(sum(1 for t in tracks if t.bpm is not None)),
            )

        self.assertEqual(checkpoints, [3, 6, 9, 10])

    def test_an_interrupted_run_resumes_from_what_it_finished(self) -> None:
        tracks = [_track(str(i)) for i in range(6)]

        def _detect_then_stop(path: str) -> float:
            if Path(path).stem == "3":
                raise KeyboardInterrupt
            return 120.0

        with patch("src.bpm_analyzer.detect_bpm", side_effect=_detect_then_stop):
            with self.assertRaises(KeyboardInterrupt):
                analyze_tracks(tracks)

        remaining = tracks_needing_bpm(tracks)
        self.assertEqual([t.id for t in remaining], ["3", "4", "5"])


class AtomicSaveTests(unittest.TestCase):
    """`analyze-bpm` rewrites the whole library repeatedly during a long run,
    so a half-written file would be a real way to lose a library."""

    def test_a_failed_write_leaves_the_previous_contents_intact(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            save_json(path, {"version": 1, "tracks": ["original"]})

            unserializable = {"version": 2, "tracks": [object()]}
            with self.assertRaises(TypeError):
                save_json(path, unserializable)

            self.assertEqual(json.loads(path.read_text()), {"version": 1, "tracks": ["original"]})

    def test_rewriting_a_file_keeps_its_permissions(self) -> None:
        # The temp file an atomic write goes through is created 0600, so
        # without carrying the mode over, every save would quietly make the
        # library private.
        with TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            save_json(path, {"n": 1})
            path.chmod(0o644)

            save_json(path, {"n": 2})

            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_a_failed_write_leaves_no_temporary_files_behind(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            save_json(path, {"ok": True})
            with self.assertRaises(TypeError):
                save_json(path, {"bad": object()})

            self.assertEqual([p.name for p in Path(directory).iterdir()], ["library.json"])


class BpmTaskTests(unittest.TestCase):
    """The worker thread must never touch the catalog: it reads each track's
    path and publishes results for the UI thread to apply."""

    def _wait(self, task, timeout: float = 5.0) -> None:
        self.assertTrue(task.done.wait(timeout), "background analysis did not finish")

    def test_results_are_published_without_mutating_the_tracks(self) -> None:
        tracks = [_track("a"), _track("b")]
        with patch("src.bpm_analyzer.detect_bpm", return_value=120.0):
            task = start_bpm_task(tracks)
            self._wait(task)

        self.assertEqual(task.drain(), {"a": 120.0, "b": 120.0})
        self.assertEqual([t.bpm for t in tracks], [None, None])

    def test_draining_takes_each_result_only_once(self) -> None:
        tracks = [_track("a")]
        with patch("src.bpm_analyzer.detect_bpm", return_value=120.0):
            task = start_bpm_task(tracks)
            self._wait(task)

        self.assertEqual(task.drain(), {"a": 120.0})
        self.assertEqual(task.drain(), {})

    def test_progress_and_failures_are_counted(self) -> None:
        tracks = [_track(str(i)) for i in range(4)]

        def _detect(path: str) -> float | None:
            return None if Path(path).stem in {"1", "2"} else 120.0

        with patch("src.bpm_analyzer.detect_bpm", side_effect=_detect):
            task = start_bpm_task(tracks)
            self._wait(task)

        self.assertEqual(task.progress(), (4, 4))
        self.assertEqual(task.failed_count(), 2)

    def test_cancelling_stops_the_run_early(self) -> None:
        tracks = [_track(str(i)) for i in range(200)]
        started = threading.Event()

        def _slow(_path: str) -> float:
            started.set()
            time.sleep(0.01)
            return 120.0

        with patch("src.bpm_analyzer.detect_bpm", side_effect=_slow):
            task = start_bpm_task(tracks)
            self.assertTrue(started.wait(5.0))
            task.cancel()
            self._wait(task)

        analyzed, total = task.progress()
        self.assertEqual(total, 200)
        self.assertLess(analyzed, 200)

    def test_a_missing_dependency_is_reported_rather_than_raised_on_the_thread(self) -> None:
        with patch("src.bpm_analyzer.require_aubio", side_effect=BpmAnalyzerUnavailableError("no aubio")):
            task = start_bpm_task([_track("a")])
            self._wait(task)

        self.assertIsInstance(task.error[0], BpmAnalyzerUnavailableError)
        self.assertEqual(task.progress(), (0, 1))


if __name__ == "__main__":
    unittest.main()
