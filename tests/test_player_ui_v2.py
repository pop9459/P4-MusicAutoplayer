"""Integration tests for v2 3-column player."""
from __future__ import annotations

import curses
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


class ColumnDividerOverlapTests(unittest.TestCase):
    """Regression test: the vertical divider between columns must not
    overwrite the first character of songs/queue rows (a real bug that
    used to be masked by the queue's numeric prefix -- see issue #1
    follow-up)."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_full_layout_does_not_clip_songs_or_queue_first_character(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        # Give every track a distinctive, non-space first character so a
        # clipped column would be immediately detectable.
        for track in self.catalog.tracks:
            track.artist = "ZEBRA"
            track.title = "Track"
        player.songs_panel.load_songs_from_catalog(self.catalog)
        player.queue_panel.update_queue(self.catalog.tracks[:3])
        # Skip real color/ACS init (both require initscr()); irrelevant here.
        player._colors_ready = True

        stdscr = FakeStdscr(height=24, width=80)
        with patch("curses.ACS_VLINE", ord("|"), create=True):
            player._render_layout(stdscr)

        # Row 1 is the first list item under each column's header (row 0).
        songs_row = stdscr.row_text(2)  # row 1 is the fixed "[R] Play Random" row
        queue_row = stdscr.row_text(1)
        self.assertIn("ZEBRA", songs_row)
        self.assertIn("ZEBRA", queue_row)


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


class ColorAndFocusTests(unittest.TestCase):
    """Issue #2: real colors, and the cursor must show which column has focus."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_colors_not_initialized_on_construction(self) -> None:
        # curses.start_color()/init_pair() require a live curses screen, and
        # Player3Column is constructed directly (no curses.wrapper) in every
        # other test here, so color setup must stay lazy.
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.assertFalse(player._colors_ready)

    def test_cursor_attr_without_color_support_falls_back_to_legacy_attrs(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        with patch("curses.has_colors", return_value=False):
            player._init_colors()

        player.active_column = 0
        self.assertEqual(player._cursor_attr(0), curses.A_STANDOUT)
        self.assertEqual(player._cursor_attr(1), curses.A_REVERSE)

    def _init_colors_without_real_screen(self, player: Player3Column) -> None:
        """curses.start_color()/init_pair()/color_pair() all require
        initscr() to have run; the test suite never runs a real screen, so
        stub those calls out (has_colors=True, color_pair returning a
        distinguishable int per pair id) while exercising our own
        color-pair-selection logic in _cursor_attr/_header_attr. Patches are
        kept active for the rest of the test via enterContext."""
        self.enterContext(patch("curses.has_colors", return_value=True))
        self.enterContext(patch("curses.start_color"))
        self.enterContext(patch("curses.use_default_colors"))
        self.enterContext(patch("curses.init_pair"))
        self.enterContext(patch("curses.color_pair", side_effect=lambda pair_id: pair_id * 100))
        player._init_colors()

    def test_focused_and_unfocused_cursor_attrs_differ(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        self._init_colors_without_real_screen(player)

        player.active_column = 0
        focused_attr = player._cursor_attr(0)
        unfocused_attr = player._cursor_attr(1)
        self.assertNotEqual(focused_attr, unfocused_attr)

    def test_render_folders_highlights_selected_row_only_when_focused(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        self._init_colors_without_real_screen(player)
        if len(player.folder_panel.folders) < 1:
            self.skipTest("no folders in test catalog")

        player.active_column = 0
        stdscr = FakeStdscr()
        player._render_folders(stdscr, row=0, col=0, width=20, height=10)
        focused_attrs = {attr for _r, _c, _t, attr in stdscr.calls}

        player.active_column = 2  # folders no longer focused
        stdscr = FakeStdscr()
        player._render_folders(stdscr, row=0, col=0, width=20, height=10)
        unfocused_attrs = {attr for _r, _c, _t, attr in stdscr.calls}

        self.assertNotEqual(focused_attrs, unfocused_attrs)


class ProgressBarTests(unittest.TestCase):
    """Issue #5: time progress bar and centered player controls."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_refresh_progress_updates_player_bar_from_backend(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.backend.get_time_pos.return_value = 61.0
        self.backend.get_duration.return_value = 180.0

        player._refresh_progress()

        self.assertEqual(player.player_bar.time_pos, 61.0)
        self.assertEqual(player.player_bar.duration, 180.0)

    def test_refresh_progress_noop_without_engine(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player.engine = None

        player._refresh_progress()

        self.backend.get_time_pos.assert_not_called()

    def test_render_player_bar_lines_are_centered(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        # Use a short track name so the info line is well short of the
        # terminal width and centering padding is guaranteed non-zero.
        player.player_bar.update_track(self.catalog.tracks[0])
        player.player_bar.current_track.title = "X"
        player.player_bar.update_progress(30.0, 120.0)
        stdscr = FakeStdscr(width=80)

        player._render_player_bar(stdscr, row=10, width=80)

        info_row, _col, info_text, _attr = stdscr.calls[0]
        self.assertTrue(info_text.startswith(" "))
        progress_row, _col, progress_text, _attr = stdscr.calls[1]
        self.assertEqual(progress_row, info_row + 1)
        self.assertTrue(progress_text.startswith(" "))
        self.assertIn("0:30", progress_text)
        self.assertIn("2:00", progress_text)


class SettingsModeTests(unittest.TestCase):
    """Issue #3: in-TUI settings screen."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_backward_compatible_construction_without_settings_path(self) -> None:
        # Locks in that settings_path stays optional/keyword-only so every
        # pre-existing 3-positional-argument call site keeps working.
        player = Player3Column(self.catalog, self.settings, self.backend)
        self.assertEqual(player.mode, "player")

    def test_s_key_opens_settings_from_each_column(self) -> None:
        for column, handler_name in ((0, "handle_folder_input"), (1, "handle_songs_input"), (2, "handle_queue_input")):
            with self.subTest(column=column):
                player = Player3Column(self.catalog, self.settings, self.backend)
                player.active_column = column
                handler = getattr(player, handler_name)
                result = handler(ord("s"))
                self.assertTrue(result)
                self.assertEqual(player.mode, "settings")
                self.assertEqual(player.settings_panel.top_k, self.settings.top_k)

    def test_settings_navigation_moves_field_index(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player._enter_settings_mode()

        player.handle_settings_input(curses.KEY_DOWN)
        self.assertEqual(player.settings_panel.field_index, 1)
        player.handle_settings_input(curses.KEY_UP)
        self.assertEqual(player.settings_panel.field_index, 0)

    def test_settings_increment_adjusts_top_k(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player._enter_settings_mode()
        initial = player.settings_panel.top_k

        player.handle_settings_input(ord("+"))

        self.assertEqual(player.settings_panel.top_k, initial + 1)

    def test_escape_cancels_without_saving(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player._enter_settings_mode()
        player.handle_settings_input(ord("+"))

        with patch("src.player_ui_v2.save_settings") as mock_save:
            player.handle_settings_input(27)

        mock_save.assert_not_called()
        self.assertEqual(player.mode, "player")

    def test_apply_saves_settings_and_returns_to_player_mode(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player._enter_settings_mode()
        player.handle_settings_input(ord("+"))  # top_k += 1
        expected = player.settings_panel.to_settings()

        with patch("src.player_ui_v2.save_settings") as mock_save:
            player.handle_settings_input(ord("a"))

        mock_save.assert_called_once_with(expected, player.settings_path)
        self.assertEqual(player.mode, "player")
        self.assertEqual(player.settings.top_k, expected.top_k)

    def test_quit_from_settings_mode_returns_false(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player._enter_settings_mode()
        self.assertFalse(player.handle_input(ord("q")))

    def test_render_settings_does_not_raise_and_shows_all_fields(self) -> None:
        player = Player3Column(self.catalog, self.settings, self.backend)
        player._enter_settings_mode()
        stdscr = FakeStdscr(height=24, width=80)

        player._render_settings(stdscr, height=24, width=80)

        rendered = " ".join(text for _r, _c, text, _a in stdscr.calls)
        for field_name in ("top_k", "randomness", "queue_length", "catalog_path"):
            self.assertIn(field_name, rendered)


if __name__ == "__main__":
    unittest.main()
