"""Smoke test for tools/recommender_report.py.

Deliberately asserts only that the report runs and prints each section --
the numbers themselves are what the tool exists to observe, so pinning them
here would just mean editing this file every time the recommender changes.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.library import Library, LibraryFolder, save_library
from src.track_analyzer import TrackRecord, build_catalog
from tools.recommender_report import main

_GENRES = ["rock", "pop", "electronic", "metal", "hip-hop"]
_SETTINGS = {"version": 1, "top_k": 5, "randomness": 0.15, "queue_length": 10}


def _library(track_count: int = 40) -> Library:
    tracks = [
        TrackRecord(
            id=f"t{i}",
            path=f"/music/t{i}.mp3",
            title=f"Track {i}",
            artist=f"Artist {i % 7}",
            genre=_GENRES[i % len(_GENRES)],
            bpm=90.0 + (i % 50),
            year=1990 + (i % 30),
            folder_id="f1",
        )
        for i in range(track_count)
    ]
    folder = LibraryFolder(
        id="f1", path="/music", display_name="music", added_at="t", last_scanned_at="t", track_count=track_count
    )
    return Library(version=1, folders=[folder], catalog=build_catalog(tracks))


class RecommenderReportTests(unittest.TestCase):
    def _run(self, *extra: str) -> str:
        with TemporaryDirectory() as directory:
            library_path = Path(directory) / "library.json"
            settings_path = Path(directory) / "settings.json"
            save_library(_library(), library_path)
            settings_path.write_text(json.dumps(_SETTINGS | {"library_path": str(library_path)}))

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--library", str(library_path), "--settings", str(settings_path), *extra])
            self.assertEqual(exit_code, 0)
            return output.getvalue()

    def test_every_section_is_reported(self) -> None:
        output = self._run("--sessions", "3", "--length", "12", "--sample", "10")

        for section in ["Coverage", "Saturation", "Sessions", "Repeats"]:
            self.assertIn(section, output)

    def test_key_measurements_are_present(self) -> None:
        output = self._run("--sessions", "3", "--length", "12", "--sample", "10")

        self.assertIn("with a tempo", output)
        self.assertIn("top-1 ties", output)
        self.assertIn("similarity to seed, last 10", output)
        self.assertIn("distinct genre labels", output)
        self.assertIn("sessions replaying a song", output)

    def test_runs_are_deterministic_so_two_reports_can_be_compared(self) -> None:
        arguments = ("--sessions", "3", "--length", "12", "--sample", "10", "--seed", "7")

        def _measurements(report: str) -> str:
            # Drop the leading "Library: <path>" line, which names a fresh
            # temporary directory on each run.
            return report.split("\n", 1)[1]

        self.assertEqual(_measurements(self._run(*arguments)), _measurements(self._run(*arguments)))

    def test_an_empty_library_is_reported_rather_than_crashing(self) -> None:
        with TemporaryDirectory() as directory:
            library_path = Path(directory) / "library.json"
            settings_path = Path(directory) / "settings.json"
            save_library(Library(version=1, folders=[], catalog=build_catalog([])), library_path)
            settings_path.write_text(json.dumps(_SETTINGS | {"library_path": str(library_path)}))

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--library", str(library_path), "--settings", str(settings_path)])

        self.assertEqual(exit_code, 1)
        self.assertIn("No enabled tracks", output.getvalue())


if __name__ == "__main__":
    unittest.main()
