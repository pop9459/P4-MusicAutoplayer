"""Tests for src/library.py: tracked folders, the merged catalog, migration,
and background scan progress."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from src.library import (
    ALL_TRACKS_FOLDER_ID,
    LIBRARY_VERSION,
    Library,
    LibraryFolder,
    add_folder,
    find_folder_by_path,
    library_needs_migration,
    list_folders,
    load_library,
    migrate_settings_to_library,
    new_library,
    remove_folder,
    rescan_folder,
    save_library,
    start_add_folder_task,
    start_rescan_folder_task,
    tracks_for_folder,
)
from src.settings import Settings
from src.track_analyzer import TrackRecord, build_catalog, save_catalog


def _make_audio_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


class LibraryRoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_round_trip(self) -> None:
        catalog = build_catalog([TrackRecord(id="t1", path="/m/t1.mp3", title="T1", folder_id="f1")])
        folder = LibraryFolder(id="f1", path="/m", display_name="m", added_at="t", last_scanned_at="t", track_count=1)
        library = Library(version=1, folders=[folder], catalog=catalog)

        restored = Library.from_dict(library.to_dict())

        # Saving always stamps the current version -- a v1 file that has been
        # loaded and written back has already been migrated in the process.
        self.assertEqual(restored.version, LIBRARY_VERSION)
        self.assertEqual(len(restored.folders), 1)
        self.assertEqual(restored.folders[0].id, "f1")
        self.assertEqual(len(restored.catalog.tracks), 1)
        self.assertEqual(restored.catalog.tracks[0].folder_id, "f1")

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.json"
            library = new_library()
            save_library(library, path)
            reloaded = load_library(path)
            self.assertEqual(reloaded.version, library.version)
            self.assertEqual(reloaded.folders, [])


class AddFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_add_folder_scans_and_stamps_folder_id(self) -> None:
        folder = self.root / "music_a"
        _make_audio_file(folder / "Artist - Song.mp3")

        library, added_folder, was_added = add_folder(new_library(), folder)

        self.assertTrue(was_added)
        self.assertEqual(added_folder.track_count, 1)
        self.assertEqual(len(library.catalog.tracks), 1)
        self.assertEqual(library.catalog.tracks[0].folder_id, added_folder.id)

    def test_add_folder_dedupes_by_resolved_path(self) -> None:
        folder = self.root / "music_a"
        _make_audio_file(folder / "Artist - Song.mp3")

        library, first_folder, was_added_1 = add_folder(new_library(), folder)
        library2, second_folder, was_added_2 = add_folder(library, folder)

        self.assertTrue(was_added_1)
        self.assertFalse(was_added_2)
        self.assertEqual(first_folder.id, second_folder.id)
        self.assertEqual(len(library2.folders), 1)
        self.assertEqual(len(library2.catalog.tracks), 1)

    def test_add_folder_missing_path_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            add_folder(new_library(), self.root / "does_not_exist")

    def test_add_folder_rebuilds_the_catalog_over_all_tracks(self) -> None:
        """Adding a folder rebuilds the merged catalog, not just the new
        folder's slice: the recorded genre/artist sets and the derived
        feature cache must cover every tracked folder afterwards."""
        folder_a = self.root / "music_a"
        _make_audio_file(folder_a / "ArtistA - SongA.mp3")
        library, folder_a_record, _ = add_folder(new_library(), folder_a)
        artists_before = list(library.catalog.artists)

        folder_b = self.root / "music_b"
        _make_audio_file(folder_b / "ArtistB - SongB.mp3")
        library, _, _ = add_folder(library, folder_b)

        self.assertGreater(len(library.catalog.artists), len(artists_before))
        existing_track = next(t for t in library.catalog.tracks if t.folder_id == folder_a_record.id)
        self.assertIn(existing_track.id, library.catalog.track_features)

    def test_add_folder_does_not_modify_the_external_folder(self) -> None:
        folder = self.root / "music_a"
        _make_audio_file(folder / "Artist - Song.mp3")
        before = set(folder.iterdir())

        add_folder(new_library(), folder)

        after = set(folder.iterdir())
        self.assertEqual(before, after)

    def test_progress_callback_reports_monotonic_progress(self) -> None:
        folder = self.root / "music_a"
        for i in range(3):
            _make_audio_file(folder / f"Artist - Song{i}.mp3")

        calls: list[tuple[int, int]] = []
        add_folder(new_library(), folder, progress_callback=lambda scanned, total: calls.append((scanned, total)))

        self.assertEqual(len(calls), 3)
        self.assertTrue(all(total == 3 for _, total in calls))
        self.assertEqual([scanned for scanned, _ in calls], [1, 2, 3])
        self.assertEqual(calls[-1], (3, 3))


class RemoveFolderTests(unittest.TestCase):
    def test_remove_folder_drops_its_tracks_and_rebuilds_feature_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder_a = root / "music_a"
            folder_b = root / "music_b"
            _make_audio_file(folder_a / "ArtistA - SongA.mp3")
            _make_audio_file(folder_b / "ArtistB - SongB.mp3")

            library, folder_a_record, _ = add_folder(new_library(), folder_a)
            library, folder_b_record, _ = add_folder(library, folder_b)
            self.assertEqual(len(library.catalog.tracks), 2)

            reduced = remove_folder(library, folder_a_record.id)

            self.assertEqual(len(reduced.folders), 1)
            self.assertEqual(reduced.folders[0].id, folder_b_record.id)
            self.assertEqual(len(reduced.catalog.tracks), 1)
            self.assertEqual(reduced.catalog.tracks[0].folder_id, folder_b_record.id)


class TracksForFolderTests(unittest.TestCase):
    def test_all_tracks_id_returns_everything(self) -> None:
        catalog = build_catalog([
            TrackRecord(id="a", path="/a.mp3", title="A", folder_id="f1"),
            TrackRecord(id="b", path="/b.mp3", title="B", folder_id="f2"),
        ])
        library = Library(version=1, folders=[], catalog=catalog)

        self.assertEqual(len(tracks_for_folder(library, ALL_TRACKS_FOLDER_ID)), 2)
        self.assertEqual([t.id for t in tracks_for_folder(library, "f1")], ["a"])


class FindFolderByPathTests(unittest.TestCase):
    def test_finds_existing_folder_by_resolved_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "music"
            _make_audio_file(folder / "Artist - Song.mp3")
            library, added, _ = add_folder(new_library(), folder)

            found = find_folder_by_path(library, folder)
            self.assertIsNotNone(found)
            self.assertEqual(found.id, added.id)

    def test_returns_none_for_untracked_path(self) -> None:
        library = new_library()
        self.assertIsNone(find_folder_by_path(library, "/not/tracked"))


class MigrationTests(unittest.TestCase):
    def test_migrates_existing_catalog_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "settings.json"
            catalog_path = root / "music" / "catalog.json"
            music_dir = root / "music"
            music_dir.mkdir(parents=True)

            old_catalog = build_catalog([
                TrackRecord(id="t1", path=str((music_dir / "song.mp3").resolve()), title="Song", artist="A", enabled=False),
            ])
            save_catalog(old_catalog, catalog_path)
            original_bytes = catalog_path.read_bytes()

            settings = Settings(
                library_path=root / "data" / "library.json",
                top_k=5,
                randomness=0.0,
                queue_length=10,
                max_consecutive_same_artist=3,
                catalog_path=catalog_path,
                music_directory=music_dir,
                music_folders=(music_dir,),
            )

            self.assertTrue(library_needs_migration(settings))
            library = migrate_settings_to_library(settings, settings_path)

            self.assertEqual(len(library.folders), 1)
            self.assertEqual(len(library.catalog.tracks), 1)
            # Manually curated enabled=False must survive migration.
            self.assertFalse(library.catalog.tracks[0].enabled)
            self.assertEqual(library.catalog.tracks[0].folder_id, library.folders[0].id)

            # Old catalog file must be left untouched.
            self.assertTrue(catalog_path.exists())
            self.assertEqual(catalog_path.read_bytes(), original_bytes)

            # library.json itself must have been written.
            self.assertTrue(settings.library_path.exists())
            self.assertFalse(library_needs_migration(settings))


class RescanFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_rescan_refreshes_duration_for_existing_tracks(self) -> None:
        folder_path = self.root / "music_a"
        _make_audio_file(folder_path / "Artist - Song.mp3")
        library, folder, _ = add_folder(new_library(), folder_path)
        original_track = library.catalog.tracks[0]
        self.assertIsNone(original_track.duration)

        refreshed_track = replace(original_track, duration=180.0)
        with mock.patch("src.library.scan_library_with_progress", return_value=[refreshed_track]):
            new_lib, track_count = rescan_folder(library, folder.id)

        self.assertEqual(track_count, 1)
        self.assertEqual(new_lib.catalog.tracks[0].duration, 180.0)

    def test_rescan_preserves_disabled_flag(self) -> None:
        folder_path = self.root / "music_a"
        _make_audio_file(folder_path / "Artist - Song.mp3")
        library, folder, _ = add_folder(new_library(), folder_path)
        original_track = library.catalog.tracks[0]
        disabled_track = replace(original_track, enabled=False)
        library = Library(
            version=library.version,
            folders=library.folders,
            catalog=build_catalog([disabled_track]),
        )

        refreshed_track = replace(original_track, duration=200.0, enabled=True)
        with mock.patch("src.library.scan_library_with_progress", return_value=[refreshed_track]):
            new_lib, _ = rescan_folder(library, folder.id)

        self.assertFalse(new_lib.catalog.tracks[0].enabled)
        self.assertEqual(new_lib.catalog.tracks[0].duration, 200.0)

    def test_rescan_drops_removed_files_and_adds_new_ones(self) -> None:
        folder_path = self.root / "music_a"
        _make_audio_file(folder_path / "Artist - Song.mp3")
        library, folder, _ = add_folder(new_library(), folder_path)

        new_track = TrackRecord(
            id="new-track",
            path=str(folder_path / "New - Track.mp3"),
            title="Track",
            folder_id=folder.id,
        )
        with mock.patch("src.library.scan_library_with_progress", return_value=[new_track]):
            new_lib, track_count = rescan_folder(library, folder.id)

        self.assertEqual(track_count, 1)
        self.assertEqual([t.id for t in new_lib.catalog.tracks], ["new-track"])

    def test_rescan_keeps_other_folders_untouched(self) -> None:
        folder_a_path = self.root / "music_a"
        _make_audio_file(folder_a_path / "ArtistA - SongA.mp3")
        library, folder_a, _ = add_folder(new_library(), folder_a_path)

        folder_b_path = self.root / "music_b"
        _make_audio_file(folder_b_path / "ArtistB - SongB.mp3")
        library, folder_b, _ = add_folder(library, folder_b_path)
        original_track_b = next(t for t in library.catalog.tracks if t.folder_id == folder_b.id)

        refreshed_track_a = replace(
            next(t for t in library.catalog.tracks if t.folder_id == folder_a.id), duration=120.0
        )
        with mock.patch("src.library.scan_library_with_progress", return_value=[refreshed_track_a]):
            new_lib, _ = rescan_folder(library, folder_a.id)

        self.assertEqual(len(new_lib.catalog.tracks), 2)
        untouched = next(t for t in new_lib.catalog.tracks if t.folder_id == folder_b.id)
        self.assertEqual(untouched.id, original_track_b.id)
        self.assertIsNone(untouched.duration)

    def test_rescan_unknown_folder_id_raises(self) -> None:
        with self.assertRaises(KeyError):
            rescan_folder(new_library(), "missing-id")

    def test_rescan_missing_path_raises(self) -> None:
        folder_path = self.root / "music_a"
        _make_audio_file(folder_path / "Artist - Song.mp3")
        library, folder, _ = add_folder(new_library(), folder_path)
        shutil.rmtree(folder_path)

        with self.assertRaises(FileNotFoundError):
            rescan_folder(library, folder.id)

    def test_progress_callback_is_forwarded(self) -> None:
        folder_path = self.root / "music_a"
        _make_audio_file(folder_path / "Artist - Song.mp3")
        library, folder, _ = add_folder(new_library(), folder_path)
        original_track = library.catalog.tracks[0]

        def _fake_scan(path, *, folder_id, progress_callback=None):
            if progress_callback is not None:
                progress_callback(1, 1)
            return [original_track]

        calls: list[tuple[int, int]] = []
        with mock.patch("src.library.scan_library_with_progress", side_effect=_fake_scan):
            rescan_folder(library, folder.id, progress_callback=lambda s, t: calls.append((s, t)))

        self.assertEqual(calls, [(1, 1)])


class RescanTaskTests(unittest.TestCase):
    def test_start_rescan_folder_task_completes_with_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder_path = Path(tmp) / "music"
            _make_audio_file(folder_path / "Artist - Song.mp3")
            library, folder, _ = add_folder(new_library(), folder_path)

            task = start_rescan_folder_task(library, folder.id)
            task.done.wait(timeout=5)

            self.assertTrue(task.done.is_set())
            self.assertEqual(task.error, [])
            self.assertEqual(len(task.result), 1)
            new_lib, track_count = task.result[0]
            self.assertEqual(track_count, 1)
            self.assertEqual(len(new_lib.catalog.tracks), 1)

    def test_start_rescan_folder_task_records_error_for_unknown_folder(self) -> None:
        task = start_rescan_folder_task(new_library(), "missing-id")
        task.done.wait(timeout=5)

        self.assertTrue(task.done.is_set())
        self.assertEqual(task.result, [])
        self.assertEqual(len(task.error), 1)
        self.assertIsInstance(task.error[0], KeyError)


class ScanTaskTests(unittest.TestCase):
    def test_start_add_folder_task_completes_with_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "music"
            _make_audio_file(folder / "Artist - Song.mp3")

            task = start_add_folder_task(new_library(), folder)
            task.done.wait(timeout=5)

            self.assertTrue(task.done.is_set())
            self.assertEqual(task.error, [])
            self.assertEqual(len(task.result), 1)
            new_lib, added_folder, was_added = task.result[0]
            self.assertTrue(was_added)
            self.assertEqual(added_folder.track_count, 1)
            self.assertEqual(len(new_lib.catalog.tracks), 1)

    def test_start_add_folder_task_records_error_for_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            task = start_add_folder_task(new_library(), missing)
            task.done.wait(timeout=5)

            self.assertTrue(task.done.is_set())
            self.assertEqual(task.result, [])
            self.assertEqual(len(task.error), 1)
            self.assertIsInstance(task.error[0], FileNotFoundError)


if __name__ == "__main__":
    unittest.main()
