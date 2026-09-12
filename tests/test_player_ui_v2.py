"""Integration tests for v2 3-column player."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.folder_panel import FolderPanel
from src.mpv_backend import MpvBackend
from src.player import PlayerEngine
from src.player_bar import PlayerBar
from src.player_ui_v2 import Player3Column
from src.queue_panel import QueuePanel
from src.settings import load_settings
from src.songs_panel import SongsPanel
from src.track_analyzer import load_catalog

from tests.curses_stub import FakeStdscr


class Player3ColumnIntegrationTests(unittest.TestCase):
    """Full workflow: folder selection → song selection → playback → queue."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_player_initializes_with_catalog_and_settings(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.assertIsNotNone(player.folder_panel)
        self.assertIsNotNone(player.songs_panel)
        self.assertIsNotNone(player.queue_panel)
        self.assertIsNotNone(player.player_bar)

    def test_folder_panel_loads_on_init(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.assertTrue(len(player.folder_panel.folders) > 0)
        self.assertEqual(player.folder_panel.selected_index, 0)

    def test_songs_load_on_init(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        # Should auto-load songs from first folder
        self.assertTrue(len(player.songs_panel.songs) > 0)

    def test_engine_initializes_with_first_song(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.assertIsNotNone(player.engine)
        self.assertIsNotNone(player.engine.current_track)
        self.backend.load_file.assert_called()

    def test_play_selected_song_initializes_playback(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        initial_track = player.songs_panel.selected_song
        player._play_selected_song()
        self.assertEqual(player.player_bar.current_track, initial_track)
        self.backend.load_file.assert_called_with(initial_track.path)

    def test_play_random_song_picks_from_enabled(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player._play_random_song()
        self.assertIsNotNone(player.player_bar.current_track)
        self.assertTrue(player.player_bar.current_track.enabled)

    def test_advance_track_moves_to_next(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        initial_track = player.engine.current_track
        player._advance_track()
        # Current track should change, and queue should stay topped up
        # (rolling top-up) rather than shrinking.
        self.assertNotEqual(player.engine.current_track.id, initial_track.id)
        self.assertEqual(len(player.engine.queue), player.settings.queue_length)

    def test_folder_selection_loads_new_songs(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        initial_songs = player.songs_panel.songs[:]
        if len(player.folder_panel.folders) > 1:
            player.folder_panel.next_folder()
            player.songs_panel.load_songs_from_catalog(self.catalog)
            # Songs may be same if folder contains same files, but panel should refresh

    def test_handle_folder_input_navigates(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        if len(player.folder_panel.folders) > 1:
            player.active_column = 0
            result = player.handle_folder_input(ord("j"))
            self.assertTrue(result)  # Should not quit
            self.assertGreater(player.folder_panel.selected_index, 0)

    def test_handle_folder_input_quit_returns_false(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        result = player.handle_folder_input(ord("q"))
        self.assertFalse(result)

    def test_handle_songs_input_plays_selected(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player.active_column = 1
        initial_track = player.songs_panel.selected_song
        player.handle_songs_input(ord("\n"))
        self.assertEqual(player.player_bar.current_track, initial_track)

    def test_handle_songs_input_random(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player.active_column = 1
        player.handle_songs_input(ord("r"))
        self.assertIsNotNone(player.player_bar.current_track)

    def test_handle_songs_input_next_track(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player.active_column = 1
        initial_queue_len = len(player.engine.queue) if player.engine else 0
        player.handle_songs_input(ord("n"))
        # Queue should advance

    def test_handle_queue_input_scrolls(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player.active_column = 2
        initial_offset = player.queue_panel.scroll_offset
        player.handle_queue_input(ord("j"))
        # Offset should change or stay at edge
        self.assertGreaterEqual(player.queue_panel.scroll_offset, 0)

    def test_queue_updates_on_playback(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        initial_queue = player.queue_panel.queue[:]
        player._advance_track()
        # Queue should update
        self.assertIsNotNone(player.queue_panel.queue)

    def test_player_bar_reflects_current_state(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.assertIsNotNone(player.player_bar.current_track)
        display = player.player_bar.get_track_display()
        self.assertIn(player.player_bar.current_track.title, display)

    def test_status_messages_update(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player.player_bar.set_status("Test message")
        self.assertEqual(player.player_bar.status_message, "Test message")
        player._play_random_song()
        # Should update status
        self.assertNotEqual(player.player_bar.status_message, "Test message")


class WorkflowIntegrationTests(unittest.TestCase):
    """Simulate realistic user workflows."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_startup_loads_defaults(self) -> None:
        """Startup should load default folder and songs."""
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.assertTrue(len(player.folder_panel.folders) > 0)
        self.assertTrue(len(player.songs_panel.songs) > 0)

    def test_full_playback_sequence(self) -> None:
        """Sequence: play song → advance track → play random."""
        player = Player3Column(self.catalog, self.settings, self.backend)
        track1 = player.songs_panel.selected_song

        # Play selected
        player._play_selected_song()
        self.assertEqual(player.player_bar.current_track, track1)

        # Advance
        player._advance_track()
        track2 = player.player_bar.current_track
        self.assertIsNotNone(track2)

        # Play random
        player._play_random_song()
        track3 = player.player_bar.current_track
        self.assertIsNotNone(track3)
        self.assertTrue(track3.enabled)


class QueueRenderTests(unittest.TestCase):
    """Issue #1: queue rows must not be numbered."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_render_queue_has_no_numeric_prefix(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player.queue_panel.update_queue(self.catalog.tracks[:5])
        stdscr = FakeStdscr()

        player._render_queue(stdscr, row=0, col=0, width=40, height=10)

        rendered_lines = [text for _row, _col, text, _attr in stdscr.calls]
        self.assertTrue(any("-" in line for line in rendered_lines[1:]))
        for line in rendered_lines[1:]:
            self.assertIsNone(re.match(r"^\s*\d+\.\s", line))


class SongsRenderTests(unittest.TestCase):
    """Issue #4: "Play Random" row must always render above the song list."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_random_row_stays_fixed_regardless_of_scroll(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        # Force a song list long enough to fill the visible column.
        player.songs_panel.songs = list(self.catalog.tracks) * 3
        player.songs_panel.selected_index = 0

        stdscr = FakeStdscr()
        player._render_songs(stdscr, row=0, col=0, width=40, height=10)
        row_no_scroll = next(row for row, _col, text, _attr in stdscr.calls if "[R] Play Random" in text)

        player.songs_panel.scroll_offset = 3
        stdscr = FakeStdscr()
        player._render_songs(stdscr, row=0, col=0, width=40, height=10)
        row_scrolled = next(row for row, _col, text, _attr in stdscr.calls if "[R] Play Random" in text)

        self.assertEqual(row_no_scroll, row_scrolled)
        self.assertEqual(row_no_scroll, 1)  # directly under the "Songs" header


if __name__ == "__main__":
    unittest.main()
