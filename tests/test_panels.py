"""Tests for v2 UI panels: folder_panel, songs_panel, queue_panel, player_bar."""
from __future__ import annotations

import unittest
from pathlib import Path

from src.folder_panel import FolderEntry, FolderPanel
from src.library import ALL_TRACKS_FOLDER_ID, Library, LibraryFolder
from src.player_bar import PlayerBar
from src.queue_panel import QueuePanel
from src.settings import Settings, load_settings
from src.songs_panel import SongsPanel
from src.track_analyzer import Catalog, TrackRecord, build_catalog, load_catalog


DEFAULT_SETTINGS = load_settings()


def _make_library() -> Library:
    """Two folders: 'a' (2 tracks) and 'b' (1 track, 1 disabled)."""
    tracks = [
        TrackRecord(id="a1", path="/music/a/1.mp3", title="A1", artist="Artist A", folder_id="folder-a"),
        TrackRecord(id="a2", path="/music/a/2.mp3", title="A2", artist="Artist A", folder_id="folder-a"),
        TrackRecord(id="b1", path="/music/b/1.mp3", title="B1", artist="Artist B", folder_id="folder-b"),
        TrackRecord(id="b2", path="/music/b/2.mp3", title="B2", artist="Artist B", folder_id="folder-b", enabled=False),
    ]
    catalog = build_catalog(tracks)
    folders = [
        LibraryFolder(id="folder-a", path="/music/a", display_name="a", added_at="t", last_scanned_at="t", track_count=2),
        LibraryFolder(id="folder-b", path="/music/b", display_name="b", added_at="t", last_scanned_at="t", track_count=2),
    ]
    return Library(version=1, folders=folders, catalog=catalog)


class FolderPanelTests(unittest.TestCase):
    def test_load_from_library_includes_all_tracks_entry_first(self) -> None:
        panel = FolderPanel()
        panel.load_from_library(_make_library())
        self.assertEqual(panel.entries[0].id, ALL_TRACKS_FOLDER_ID)
        self.assertEqual(panel.entries[0].track_count, 4)
        self.assertEqual([entry.id for entry in panel.entries[1:]], ["folder-a", "folder-b"])

    def test_selected_folder_is_all_tracks_by_default(self) -> None:
        panel = FolderPanel()
        panel.load_from_library(_make_library())
        self.assertEqual(panel.selected_index, 0)
        self.assertEqual(panel.selected_entry.id, ALL_TRACKS_FOLDER_ID)

    def test_next_folder_increments_index(self) -> None:
        panel = FolderPanel()
        panel.load_from_library(_make_library())
        initial_idx = panel.selected_index
        panel.next_folder()
        self.assertGreater(panel.selected_index, initial_idx)

    def test_previous_folder_decrements_index(self) -> None:
        panel = FolderPanel()
        panel.load_from_library(_make_library())
        panel.next_folder()
        panel.previous_folder()
        self.assertEqual(panel.selected_index, 0)


class SongsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.library = _make_library()

    def _all_tracks_entry(self) -> FolderEntry:
        return FolderEntry(id=ALL_TRACKS_FOLDER_ID, display_name="All Tracks", path=None, track_count=4)

    def _folder_a_entry(self) -> FolderEntry:
        return FolderEntry(id="folder-a", display_name="a", path="/music/a", track_count=2)

    def _folder_b_entry(self) -> FolderEntry:
        return FolderEntry(id="folder-b", display_name="b", path="/music/b", track_count=2)

    def test_load_songs_from_library_all_tracks_excludes_disabled(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        self.assertEqual(len(panel.songs), 3)
        for track in panel.songs:
            self.assertTrue(track.enabled)

    def test_load_songs_from_library_filters_by_folder(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._folder_a_entry())
        self.assertEqual([track.id for track in panel.songs], ["a1", "a2"])

    def test_load_songs_from_library_excludes_disabled_within_folder(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._folder_b_entry())
        self.assertEqual([track.id for track in panel.songs], ["b1"])

    def test_switching_folder_changes_song_list(self) -> None:
        """Regression test: folder selection must actually filter songs."""
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._folder_a_entry())
        songs_for_a = [track.id for track in panel.songs]
        panel.load_songs_from_library(self.library, self._folder_b_entry())
        songs_for_b = [track.id for track in panel.songs]
        self.assertNotEqual(songs_for_a, songs_for_b)

    def test_selected_song_is_first_by_default(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        self.assertEqual(panel.selected_index, 0)
        self.assertIsNotNone(panel.selected_song)
        self.assertEqual(panel.selected_song, panel.songs[0])

    def test_next_song_increments_index(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.next_song()
        self.assertEqual(panel.selected_index, 1)

    def test_previous_song_decrements_index(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.next_song()
        panel.previous_song()
        self.assertEqual(panel.selected_index, 0)

    def test_get_visible_songs_returns_tuples(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        visible = list(panel.get_visible_songs(10))
        self.assertTrue(len(visible) > 0)
        for track, idx, is_selected in visible:
            self.assertIsInstance(track, TrackRecord)
            self.assertIsInstance(idx, int)
            self.assertIsInstance(is_selected, bool)


class QueuePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(Path("testTracks/catalog.json"))

    def test_initial_queue_is_empty(self) -> None:
        panel = QueuePanel()
        self.assertEqual(len(panel.queue), 0)

    def test_update_queue_populates_tracks(self) -> None:
        panel = QueuePanel()
        test_queue = self.catalog.tracks[:5]
        panel.update_queue(test_queue)
        self.assertEqual(len(panel.queue), len(test_queue))

    def test_scroll_down_increments_offset(self) -> None:
        panel = QueuePanel()
        test_queue = self.catalog.tracks[:10]
        panel.update_queue(test_queue)
        panel.scroll_down()
        self.assertGreater(panel.scroll_offset, 0)

    def test_scroll_up_decrements_offset(self) -> None:
        panel = QueuePanel()
        test_queue = self.catalog.tracks[:10]
        panel.update_queue(test_queue)
        panel.scroll_down()
        panel.scroll_down()
        panel.scroll_up()
        self.assertGreaterEqual(panel.scroll_offset, 0)

    def test_get_visible_queue_returns_tuples(self) -> None:
        panel = QueuePanel()
        test_queue = self.catalog.tracks[:5]
        panel.update_queue(test_queue)
        visible = list(panel.get_visible_queue(3))
        self.assertTrue(len(visible) > 0)
        for track, idx in visible:
            self.assertIsInstance(track, TrackRecord)
            self.assertIsInstance(idx, int)

    def test_update_queue_stores_current_track(self) -> None:
        panel = QueuePanel()
        current = self.catalog.tracks[0]
        upcoming = self.catalog.tracks[1:4]
        panel.update_queue(upcoming, current)
        self.assertEqual(panel.current_track, current)
        self.assertEqual(len(panel.queue), 3)

    def test_update_queue_current_track_defaults_to_none(self) -> None:
        panel = QueuePanel()
        panel.update_queue(self.catalog.tracks[:3])
        self.assertIsNone(panel.current_track)


class PlayerBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(Path("testTracks/catalog.json"))
        cls.track = cls.catalog.tracks[0]

    def test_initial_state_is_idle(self) -> None:
        bar = PlayerBar()
        self.assertIsNone(bar.current_track)
        self.assertFalse(bar.paused)

    def test_update_track_sets_current_track(self) -> None:
        bar = PlayerBar()
        bar.update_track(self.track)
        self.assertEqual(bar.current_track, self.track)

    def test_set_paused_changes_state(self) -> None:
        bar = PlayerBar()
        bar.set_paused(True)
        self.assertTrue(bar.paused)
        bar.set_paused(False)
        self.assertFalse(bar.paused)

    def test_set_status_stores_message(self) -> None:
        bar = PlayerBar()
        bar.set_status("Test message")
        self.assertEqual(bar.status_message, "Test message")

    def test_get_track_display_with_no_track(self) -> None:
        bar = PlayerBar()
        display = bar.get_track_display()
        self.assertIsInstance(display, str)

    def test_get_track_display_with_track(self) -> None:
        bar = PlayerBar()
        bar.update_track(self.track)
        display = bar.get_track_display()
        self.assertIn(self.track.title, display)

    def test_get_state_display_shows_play_pause(self) -> None:
        bar = PlayerBar()
        bar.set_paused(True)
        display = bar.get_state_display()
        self.assertIn("PAUSED", display)
        bar.set_paused(False)
        display = bar.get_state_display()
        self.assertIn("PLAYING", display)

    def test_get_progress_display_with_no_track(self) -> None:
        bar = PlayerBar()
        display = bar.get_progress_display()
        self.assertIn("--:--", display)

    def test_get_progress_display_at_start(self) -> None:
        bar = PlayerBar()
        bar.update_progress(0.0, 180.0)
        display = bar.get_progress_display()
        self.assertIn("0:00", display)
        self.assertIn("3:00", display)

    def test_get_progress_display_midway(self) -> None:
        bar = PlayerBar()
        bar.update_progress(90.0, 180.0)
        display = bar.get_progress_display(bar_width=10)
        self.assertIn("1:30", display)
        self.assertIn("3:00", display)
        self.assertIn("=", display)

    def test_get_progress_display_at_end(self) -> None:
        bar = PlayerBar()
        bar.update_progress(180.0, 180.0)
        display = bar.get_progress_display(bar_width=10)
        self.assertIn("3:00", display)
        self.assertNotIn("-", display.split("]")[0])

    def test_get_progress_display_with_zero_duration_is_safe(self) -> None:
        bar = PlayerBar()
        bar.update_progress(0.0, 0.0)
        display = bar.get_progress_display()
        self.assertIn("--:--", display)

    def test_update_track_resets_progress(self) -> None:
        bar = PlayerBar()
        bar.update_progress(90.0, 180.0)
        bar.update_track(self.track)
        self.assertIsNone(bar.time_pos)
        self.assertIsNone(bar.duration)


if __name__ == "__main__":
    unittest.main()
