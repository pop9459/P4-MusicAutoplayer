from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import argparse

from src.cli import _needs_settings, main
from src.track_analyzer import TrackRecord, build_catalog, save_catalog


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temporary_directory.name) / "tracks.json"
        catalog = build_catalog([
            TrackRecord(id="current", path="/music/current.mp3", title="Current", artist="A", genre="pop", bpm=120, year=2020),
            TrackRecord(id="match", path="/music/match.mp3", title="Match", artist="A", genre="pop", bpm=121, year=2021),
            TrackRecord(id="disabled", path="/music/disabled.mp3", title="Disabled", artist="A", genre="pop", bpm=120, year=2020, enabled=False),
        ])
        save_catalog(catalog, self.catalog_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _run(self, *arguments: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        return output.getvalue()

    def test_summary_displays_catalog_statistics(self) -> None:
        output = self._run("summary", "--catalog", str(self.catalog_path))
        self.assertIn("Tracks: 3 (2 enabled, 1 disabled)", output)
        self.assertIn("grouped into a family", output)
        self.assertIn("Tracks with an ungrouped genre:", output)

    def test_list_tracks_honors_enabled_only(self) -> None:
        output = self._run("list-tracks", "--catalog", str(self.catalog_path), "--enabled-only")
        self.assertIn("current", output)
        self.assertNotIn("disabled  [disabled]", output)

    def test_recommendation_displays_ranked_eligible_candidates(self) -> None:
        output = self._run("recommend", "--catalog", str(self.catalog_path), "--track-id", "current")
        self.assertIn("Eligible candidates analyzed: 1", output)
        self.assertIn("match", output)
        self.assertNotIn("disabled  [disabled]", output)
        self.assertIn("Recommendation", output)


    def test_queue_displays_a_shorter_non_repeating_queue_when_exhausted(self) -> None:
        output = self._run("queue", "--catalog", str(self.catalog_path), "--track-id", "current", "--length", "10")
        self.assertIn("Starting track: current", output)
        self.assertIn("Generated 1 of 10 requested queue tracks.", output)
        self.assertIn("match", output)
        self.assertNotIn("disabled  [disabled]", output)


class AddFolderCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.music_dir = self.root / "music"
        self.music_dir.mkdir()
        (self.music_dir / "Artist - Song.mp3").write_bytes(b"")
        self.library_path = self.root / "library.json"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _run(self, *arguments: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        return output.getvalue()

    def test_add_folder_creates_library_with_one_folder(self) -> None:
        output = self._run("add-folder", "--path", str(self.music_dir), "--library", str(self.library_path))
        self.assertIn("Added folder", output)
        self.assertTrue(self.library_path.exists())

        list_output = self._run("list-folders", "--library", str(self.library_path))
        self.assertIn("1 folders, 1 tracks total.", list_output)

    def test_add_folder_twice_is_a_no_op(self) -> None:
        self._run("add-folder", "--path", str(self.music_dir), "--library", str(self.library_path))
        second_output = self._run("add-folder", "--path", str(self.music_dir), "--library", str(self.library_path))
        self.assertIn("already tracked", second_output)

        list_output = self._run("list-folders", "--library", str(self.library_path))
        self.assertIn("1 folders,", list_output)

    def test_remove_folder_drops_its_tracks(self) -> None:
        self._run("add-folder", "--path", str(self.music_dir), "--library", str(self.library_path))
        from src.library import load_library
        folder_id = load_library(self.library_path).folders[0].id

        output = self._run("remove-folder", "--folder-id", folder_id, "--library", str(self.library_path))
        self.assertIn("Removed folder", output)

        library = load_library(self.library_path)
        self.assertEqual(library.folders, [])
        self.assertEqual(library.catalog.tracks, [])

    def test_add_folder_missing_path_errors(self) -> None:
        with self.assertRaises(SystemExit):
            main(["add-folder", "--path", str(self.root / "missing"), "--library", str(self.library_path)])

    def test_external_music_folder_is_left_untouched(self) -> None:
        before = set(self.music_dir.iterdir())
        self._run("add-folder", "--path", str(self.music_dir), "--library", str(self.library_path))
        after = set(self.music_dir.iterdir())
        self.assertEqual(before, after)

    def test_rescan_folder_reports_summary_and_keeps_track_count(self) -> None:
        from src.library import load_library

        self._run("add-folder", "--path", str(self.music_dir), "--library", str(self.library_path))
        folder_id = load_library(self.library_path).folders[0].id

        output = self._run("rescan-folder", "--folder-id", folder_id, "--library", str(self.library_path))
        self.assertIn("Rescanned folder", output)

        library = load_library(self.library_path)
        self.assertEqual(len(library.catalog.tracks), 1)

    def test_rescan_folder_unknown_id_errors(self) -> None:
        self._run("add-folder", "--path", str(self.music_dir), "--library", str(self.library_path))
        with self.assertRaises(SystemExit):
            main(["rescan-folder", "--folder-id", "missing-id", "--library", str(self.library_path)])


class NeedsSettingsTests(unittest.TestCase):
    """Pins `_needs_settings`'s branch behavior so it can be safely refactored
    later (see CLAUDE.md refinement notes on the conditional chain)."""

    @staticmethod
    def _args(command: str, **overrides) -> argparse.Namespace:
        defaults = {
            "command": command,
            "library": None,
            "catalog": None,
            "music_dir": None,
            "top_k": None,
            "randomness": None,
            "length": None,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_play_always_needs_settings(self) -> None:
        self.assertTrue(_needs_settings(self._args("play")))

    def test_library_commands_need_settings_only_without_explicit_library(self) -> None:
        for command in ("add-folder", "list-folders", "remove-folder", "rescan-folder"):
            self.assertTrue(_needs_settings(self._args(command, library=None)))
            self.assertFalse(_needs_settings(self._args(command, library=Path("lib.json"))))

    def test_debug_command_needs_settings_when_catalog_missing(self) -> None:
        self.assertTrue(_needs_settings(self._args("summary", catalog=None)))

    def test_debug_command_needs_settings_when_catalog_absent_and_no_music_dir(self) -> None:
        missing_catalog = Path(tempfile.gettempdir()) / "does-not-exist-catalog.json"
        self.assertTrue(_needs_settings(self._args("summary", catalog=missing_catalog, music_dir=None)))

    def test_build_catalog_needs_settings_only_without_music_dir(self) -> None:
        existing_catalog = Path(__file__)  # any existing path
        self.assertTrue(_needs_settings(self._args("build-catalog", catalog=existing_catalog, music_dir=None)))
        self.assertFalse(_needs_settings(self._args("build-catalog", catalog=existing_catalog, music_dir=Path("music"))))

    def test_recommend_and_queue_need_settings_when_top_k_or_randomness_missing(self) -> None:
        existing_catalog = Path(__file__)
        for command in ("recommend", "queue"):
            self.assertTrue(_needs_settings(self._args(command, catalog=existing_catalog, top_k=None, randomness=0.1, length=5)))
            self.assertTrue(_needs_settings(self._args(command, catalog=existing_catalog, top_k=5, randomness=None, length=5)))
            self.assertFalse(_needs_settings(self._args(command, catalog=existing_catalog, top_k=5, randomness=0.1, length=5)))

    def test_queue_needs_settings_when_length_missing(self) -> None:
        existing_catalog = Path(__file__)
        self.assertTrue(_needs_settings(self._args("queue", catalog=existing_catalog, top_k=5, randomness=0.1, length=None)))
        self.assertFalse(_needs_settings(self._args("queue", catalog=existing_catalog, top_k=5, randomness=0.1, length=5)))


if __name__ == "__main__":
    unittest.main()
