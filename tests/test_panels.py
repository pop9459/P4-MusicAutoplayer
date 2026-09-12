"""Tests for v2 UI panels: folder_panel, songs_panel, queue_panel, player_bar."""
from __future__ import annotations

import unittest
from pathlib import Path

from src.folder_panel import FolderPanel
from src.player_bar import PlayerBar
from src.queue_panel import QueuePanel
from src.settings import Settings, load_settings
from src.songs_panel import SongsPanel
from src.track_analyzer import Catalog, TrackRecord, load_catalog


DEFAULT_SETTINGS = load_settings()


class FolderPanelTests(unittest.TestCase):
    def test_load_folders_from_settings(self) -> None:
        panel = FolderPanel()
        panel.load_folders_from_settings(DEFAULT_SETTINGS)
        self.assertTrue(len(panel.folders) > 0)

    def test_selected_folder_is_first_by_default(self) -> None:
        panel = FolderPanel()
        panel.load_folders_from_settings(DEFAULT_SETTINGS)
        self.assertEqual(panel.selected_index, 0)
        self.assertIsNotNone(panel.selected_folder)

    def test_next_folder_increments_index(self) -> None:
        panel = FolderPanel()
        panel.load_folders_from_settings(DEFAULT_SETTINGS)
        if len(panel.folders) > 1:
            initial_idx = panel.selected_index
            panel.next_folder()
            self.assertGreater(panel.selected_index, initial_idx)

    def test_previous_folder_decrements_index(self) -> None:
        panel = FolderPanel()
        panel.load_folders_from_settings(DEFAULT_SETTINGS)
        if len(panel.folders) > 1:
            panel.next_folder()
            panel.previous_folder()
            self.assertEqual(panel.selected_index, 0)


class SongsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(Path("testTracks/catalog.json"))

    def test_load_songs_from_catalog(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_catalog(self.catalog)
        self.assertTrue(len(panel.songs) > 0)
        for track in panel.songs:
            self.assertTrue(track.enabled)

    def test_selected_song_is_first_by_default(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_catalog(self.catalog)
        self.assertEqual(panel.selected_index, 0)
        self.assertIsNotNone(panel.selected_song)
        self.assertEqual(panel.selected_song, panel.songs[0])

    def test_next_song_increments_index(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_catalog(self.catalog)
        if len(panel.songs) > 1:
            panel.next_song()
            self.assertEqual(panel.selected_index, 1)

    def test_previous_song_decrements_index(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_catalog(self.catalog)
        if len(panel.songs) > 1:
            panel.next_song()
            panel.previous_song()
            self.assertEqual(panel.selected_index, 0)

    def test_get_visible_songs_returns_tuples(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_catalog(self.catalog)
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
        for track, idx, is_first in visible:
            self.assertIsInstance(track, TrackRecord)
            self.assertIsInstance(idx, int)
            self.assertIsInstance(is_first, bool)


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
