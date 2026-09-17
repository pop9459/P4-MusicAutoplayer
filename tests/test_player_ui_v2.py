"""Integration tests for v2 3-column player."""
from __future__ import annotations

import curses
import dataclasses
import re
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.folder_panel import FolderPanel
from src.library import Library, LibraryFolder
from src.mpv_backend import MpvBackend
from src.player import PlayerEngine
from src.player_bar import PlayerBar
from src.player_ui_v2 import MIN_QUEUE_LENGTH, Player3Column
from src.queue_panel import QueuePanel
from src.settings import load_settings
from src.songs_panel import SongsPanel
from src.track_analyzer import load_catalog

from tests.curses_stub import FakeStdscr



def _library_from_catalog(catalog):
    """Wrap a loaded testTracks catalog in a single-folder Library, mutating
    folder_id in place so `catalog` and `library.catalog` stay the same
    object (tests that mutate self.catalog.tracks afterward rely on this)."""
    for track in catalog.tracks:
        track.folder_id = "testtracks"
    folder = LibraryFolder(
        id="testtracks", path="testTracks", display_name="testTracks",
        added_at="t", last_scanned_at="t", track_count=len(catalog.tracks),
    )
    return Library(version=1, folders=[folder], catalog=catalog)

class Player3ColumnIntegrationTests(unittest.TestCase):
    """Full workflow: folder selection → song selection → playback → queue."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_player_initializes_with_catalog_and_settings(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertIsNotNone(player.folder_panel)
        self.assertIsNotNone(player.songs_panel)
        self.assertIsNotNone(player.queue_panel)
        self.assertIsNotNone(player.player_bar)

    def test_folder_panel_loads_on_init(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertTrue(len(player.folder_panel.entries) > 0)
        self.assertEqual(player.folder_panel.selected_index, 0)

    def test_songs_load_on_init(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        # Should auto-load songs from first folder
        self.assertTrue(len(player.songs_panel.songs) > 0)

    def test_engine_is_none_until_song_selected(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertIsNone(player.engine)
        self.backend.load_file.assert_not_called()

    def test_construction_does_not_start_playback(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertIsNone(player.engine)
        self.backend.load_file.assert_not_called()
        self.assertIsNone(player.player_bar.current_track)

    def test_play_selected_song_initializes_playback(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        initial_track = player.songs_panel.selected_song
        player._play_selected_song()
        self.assertEqual(player.player_bar.current_track, initial_track)
        self.backend.load_file.assert_called_with(initial_track.path)

    def test_play_random_song_picks_from_enabled(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_random_song()
        self.assertIsNotNone(player.player_bar.current_track)
        self.assertTrue(player.player_bar.current_track.enabled)

    def test_advance_track_moves_to_next(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._queue_task.done.wait(timeout=5)
        initial_track = player.engine.current_track
        player._advance_track()
        player._queue_task.done.wait(timeout=5)
        # Current track should change, and queue should stay topped up
        # (rolling top-up) rather than shrinking.
        self.assertNotEqual(player.engine.current_track.id, initial_track.id)
        self.assertEqual(len(player.engine.queue), player.settings.queue_length)

    def test_folder_selection_loads_new_songs(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        initial_songs = player.songs_panel.songs[:]
        if len(player.folder_panel.entries) > 1:
            player.folder_panel.next_folder()
            player.songs_panel.load_songs_from_library(self.library, player.folder_panel.selected_entry)
            # Songs may be same if folder contains same files, but panel should refresh

    def test_handle_folder_input_navigates(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        if len(player.folder_panel.entries) > 1:
            player.active_column = 0
            result = player.handle_folder_input(ord("j"))
            self.assertTrue(result)  # Should not quit
            self.assertGreater(player.folder_panel.selected_index, 0)
        self.assertIsNone(player.engine)

    def test_handle_folder_input_enter_does_not_play(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.active_column = 0
        result = player.handle_folder_input(ord("\n"))
        self.assertTrue(result)
        self.assertEqual(player.active_column, 1)
        self.assertIsNone(player.engine)
        self.backend.load_file.assert_not_called()

    def test_handle_folder_input_space_toggles_pause(self) -> None:
        """Issue #16: playback controls must work from the folder column,
        not just once the songs column has focus. Space is now the global
        pause key everywhere, so it no longer moves focus to songs."""
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player.active_column = 0
        self.backend.toggle_pause.return_value = True

        result = player.handle_folder_input(ord(" "))

        self.assertTrue(result)
        self.assertEqual(player.active_column, 0)
        self.backend.toggle_pause.assert_called_once()
        self.assertTrue(player.player_bar.paused)

    def test_handle_folder_input_n_advances_track(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._queue_task.done.wait(timeout=5)
        player.active_column = 0
        initial_track = player.engine.current_track

        player.handle_folder_input(ord("n"))

        self.assertNotEqual(player.engine.current_track.id, initial_track.id)

    def test_handle_folder_input_r_plays_random(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.active_column = 0

        result = player.handle_folder_input(ord("r"))

        self.assertTrue(result)
        self.assertIsNotNone(player.player_bar.current_track)

    def test_handle_folder_input_quit_returns_false(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        result = player.handle_folder_input(ord("q"))
        self.assertFalse(result)

    def test_handle_songs_input_plays_selected(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.active_column = 1
        initial_track = player.songs_panel.selected_song
        player.handle_songs_input(ord("\n"))
        self.assertEqual(player.player_bar.current_track, initial_track)

    def test_handle_songs_input_random(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.active_column = 1
        player.handle_songs_input(ord("r"))
        self.assertIsNotNone(player.player_bar.current_track)

    def test_handle_songs_input_slash_opens_search_mode(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.active_column = 1
        player.handle_songs_input(ord("/"))
        self.assertTrue(player._search_mode)

    def test_handle_folder_input_slash_opens_search_mode(self) -> None:
        """Search is reachable from either column -- it's a top-level bar,
        not a songs-column-specific control."""
        player = Player3Column(self.library, self.settings, self.backend)
        player.active_column = 0
        player.handle_folder_input(ord("/"))
        self.assertTrue(player._search_mode)

    def test_search_typing_filters_live_on_each_keystroke(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.handle_input(ord("/"))

        for char in "mood":
            player.handle_input(ord(char))

        self.assertEqual(player.songs_panel.filter_query, "mood")
        self.assertTrue(player._search_mode)
        self.assertTrue(all("mood" in t.title.lower() for t in player.songs_panel.songs))

    def test_search_backspace_removes_last_character_live(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.handle_input(ord("/"))
        for char in "mood":
            player.handle_input(ord(char))

        player.handle_input(curses.KEY_BACKSPACE)

        self.assertEqual(player.songs_panel.filter_query, "moo")

    def test_search_enter_closes_box_and_keeps_filter(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.handle_input(ord("/"))
        player.handle_input(ord("x"))

        player.handle_input(ord("\n"))

        self.assertFalse(player._search_mode)
        self.assertEqual(player.songs_panel.filter_query, "x")

    def test_search_escape_closes_box_and_clears_filter(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.handle_input(ord("/"))
        player.handle_input(ord("x"))

        player.handle_input(27)

        self.assertFalse(player._search_mode)
        self.assertEqual(player.songs_panel.filter_query, "")

    def test_typing_q_while_searching_does_not_quit(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.handle_input(ord("/"))

        result = player.handle_input(ord("q"))

        self.assertTrue(result)
        self.assertTrue(player._search_mode)
        self.assertEqual(player.songs_panel.filter_query, "q")

    def test_handle_songs_input_next_track(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player.active_column = 1
        initial_queue_len = len(player.engine.queue) if player.engine else 0
        player.handle_songs_input(ord("n"))
        # Queue should advance

    def test_handle_songs_input_o_cycles_sort(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.active_column = 1
        self.assertEqual(player.songs_panel.sort_mode, "default")

        player.handle_songs_input(ord("o"))

        self.assertEqual(player.songs_panel.sort_mode, "title")

    def test_handle_songs_input_comma_goes_back(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player.active_column = 1
        original_track = player.engine.current_track
        player.handle_songs_input(ord("n"))
        self.assertNotEqual(player.engine.current_track.id, original_track.id)

        player.handle_songs_input(ord(","))

        self.assertEqual(player.engine.current_track.id, original_track.id)
        self.backend.load_file.assert_called_with(original_track.path)

    def test_handle_songs_input_comma_with_no_history_reports_status(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player.active_column = 1

        player.handle_songs_input(ord(","))

        self.assertEqual(player.player_bar.status_message, "No previous track.")

    def test_queue_updates_on_playback(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        initial_queue = player.queue_panel.queue[:]
        player._advance_track()
        # Queue should update
        self.assertIsNotNone(player.queue_panel.queue)

    def test_player_bar_reflects_current_state(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        self.assertIsNotNone(player.player_bar.current_track)
        display = player.player_bar.get_track_display()
        self.assertIn(player.player_bar.current_track.title, display)

    def test_status_messages_update(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.player_bar.set_status("Test message")
        self.assertEqual(player.player_bar.status_message, "Test message")
        player._play_random_song()
        # Should update status
        self.assertNotEqual(player.player_bar.status_message, "Test message")

    def test_playback_starts_before_queue_generation_completes(self) -> None:
        """`_init_engine_with_song` must call `backend.load_file` before
        queue generation runs, so a slow full-catalog scan on a large
        library doesn't delay when audio starts."""
        player = Player3Column(self.library, self.settings, self.backend)
        call_order: list[str] = []
        self.backend.load_file.side_effect = lambda path: call_order.append("load_file")

        with patch("src.player.generate_queue_steps", side_effect=lambda *a, **k: call_order.append("generate_queue_steps") or iter([])):
            player._play_selected_song()
            player._queue_task.done.wait(timeout=5)

        self.assertEqual(call_order, ["load_file", "generate_queue_steps"])

    def test_advance_track_starts_playback_before_topping_up_queue(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._queue_task.done.wait(timeout=5)  # initial queue ready
        call_order: list[str] = []
        self.backend.load_file.side_effect = lambda path: call_order.append("load_file")

        filler_track = self.catalog.tracks[0]
        with patch("src.player.recommend_next_track", side_effect=lambda *a, **k: call_order.append("recommend_next_track") or filler_track):
            player._advance_track()
            player._queue_task.done.wait(timeout=5)

        self.assertEqual(call_order[0], "load_file")
        self.assertIn("recommend_next_track", call_order)

    def test_queue_reveals_one_track_at_a_time(self) -> None:
        """The queue-build background task should report growing progress
        one track at a time (via _poll_queue_task), not jump straight from
        empty to the finished list, so the queue column visibly fills in."""
        player = Player3Column(self.library, self.settings, self.backend)
        player._stdscr = FakeStdscr(height=24, width=80)
        player._colors_ready = True

        import src.player as player_module

        original_generate_queue_steps = player_module.generate_queue_steps

        def _slow_generate_queue_steps(*args, **kwargs):
            for track in original_generate_queue_steps(*args, **kwargs):
                time.sleep(0.02)
                yield track

        queue_lengths_seen = []
        original_update_queue = player.queue_panel.update_queue

        def _tracking_update_queue(queue, current_track=None):
            original_update_queue(queue, current_track)
            queue_lengths_seen.append(len(queue))

        player.queue_panel.update_queue = _tracking_update_queue

        with patch("src.player.generate_queue_steps", side_effect=_slow_generate_queue_steps):
            player._play_selected_song()
            deadline = time.time() + 5
            while player._queue_task is not None and not player._queue_task.done.is_set() and time.time() < deadline:
                player._poll_queue_task()
                time.sleep(0.01)
            player._poll_queue_task()

        self.assertGreater(len(queue_lengths_seen), 1)
        self.assertEqual(queue_lengths_seen, sorted(queue_lengths_seen))
        self.assertEqual(queue_lengths_seen[-1], len(player.engine.queue))

    def test_play_selected_song_does_not_block_on_queue_generation(self) -> None:
        """Regression test for issue #15: selecting a track must return
        immediately even when queue generation is slow, instead of blocking
        the calling (UI) thread until the full-catalog scan finishes."""
        player = Player3Column(self.library, self.settings, self.backend)

        def _slow_generate_queue_steps(*args, **kwargs):
            for _ in range(3):
                time.sleep(2.0)
                yield self.catalog.tracks[0]

        with patch("src.player.generate_queue_steps", side_effect=_slow_generate_queue_steps):
            started = time.time()
            player._play_selected_song()
            elapsed = time.time() - started

        self.assertLess(elapsed, 1.0)
        self.assertIsNotNone(player._queue_task)
        self.assertFalse(player._queue_task.done.is_set())
        player._queue_task.cancel.set()
        player._queue_task.done.wait(timeout=5)

    def test_advance_track_cancels_stale_queue_task(self) -> None:
        """A second `_advance_track()` fired before the previous top-up
        finishes must cancel the stale background task rather than let two
        threads race on `engine.queue`."""
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._queue_task.done.wait(timeout=5)  # initial queue ready

        def _slow_recommend_next_track(*args, **kwargs):
            time.sleep(0.2)
            return self.catalog.tracks[0]

        with patch("src.player.recommend_next_track", side_effect=_slow_recommend_next_track):
            player._advance_track()
            old_task = player._queue_task
            self.assertIsNotNone(old_task)
            self.assertFalse(old_task.done.is_set())

            player._advance_track()

        self.assertTrue(old_task.cancel.is_set())
        self.assertIsNot(player._queue_task, old_task)
        old_task.done.wait(timeout=5)
        if player._queue_task is not None:
            player._queue_task.cancel.set()
            player._queue_task.done.wait(timeout=5)

    def test_poll_queue_task_reports_error(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)

        def _raising_generate_queue_steps(*args, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover - unreachable, makes this a generator

        with patch("src.player.generate_queue_steps", side_effect=_raising_generate_queue_steps):
            player._play_selected_song()
            player._queue_task.done.wait(timeout=5)
            player._poll_queue_task()

        self.assertIn("Queue build failed", player.player_bar.status_message)
        self.assertIsNone(player._queue_task)

    def test_effective_queue_length_uses_terminal_height(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.settings = dataclasses.replace(player.settings, queue_length=1)
        player._term_height = 40  # visible rows = 40 - 8 = 32

        self.assertEqual(player._effective_queue_length(), 32)

    def test_effective_queue_length_floors_at_minimum_of_ten(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._term_height = 10  # visible rows = max(0, 10-6) = 4
        player.settings = dataclasses.replace(player.settings, queue_length=1)

        self.assertEqual(player._effective_queue_length(), MIN_QUEUE_LENGTH)

    def test_effective_queue_length_respects_configured_setting_as_floor(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._term_height = 10  # visible rows = max(0, 10-6) = 4
        player.settings = dataclasses.replace(player.settings, queue_length=15)

        self.assertEqual(player._effective_queue_length(), 15)


class WorkflowIntegrationTests(unittest.TestCase):
    """Simulate realistic user workflows."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_startup_loads_defaults(self) -> None:
        """Startup should load default folder and songs."""
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertTrue(len(player.folder_panel.entries) > 0)
        self.assertTrue(len(player.songs_panel.songs) > 0)

    def test_full_playback_sequence(self) -> None:
        """Sequence: play song → advance track → play random."""
        player = Player3Column(self.library, self.settings, self.backend)
        # Engine should be None until we explicitly select a song
        self.assertIsNone(player.engine)
        track1 = player.songs_panel.selected_song

        # Play selected
        player._play_selected_song()
        self.assertEqual(player.player_bar.current_track, track1)
        self.assertIsNotNone(player.engine)

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
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_full_layout_does_not_clip_songs_or_queue_first_character(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        # Give every track a distinctive, non-space first character so a
        # clipped column would be immediately detectable.
        for track in self.catalog.tracks:
            track.artist = "ZEBRA"
            track.title = "Track"
        player.songs_panel.load_songs_from_library(self.library, player.folder_panel.selected_entry)
        player.queue_panel.update_queue(self.catalog.tracks[:3])
        # Skip real color/ACS init (both require initscr()); irrelevant here.
        player._colors_ready = True

        stdscr = FakeStdscr(height=24, width=80)
        with patch("curses.ACS_VLINE", ord("|"), create=True):
            player._render_layout(stdscr)

        # Row 0 is the top search bar. Songs: row 1 is header, row 2 is
        # path, row 3 is count, row 4 is "[R] Play Random", row 5 is first
        # song. Queue: row 1 is "Now Playing" header, row 2 is current
        # track (or "(none)"), row 3 is "Queue (N)" header, row 4 is first
        # upcoming track.
        songs_row = stdscr.row_text(5)  # first song item
        queue_row = stdscr.row_text(4)  # first upcoming queue item
        self.assertIn("ZEBRA", songs_row)
        self.assertIn("ZEBRA", queue_row)

        # row_text() returns the whole terminal row (all 3 columns share
        # one character grid), so the checks above alone can pass on a
        # false positive -- e.g. the queue column's own "ZEBRA - Track"
        # text landing on the same physical row as the songs column,
        # regardless of what the songs column itself drew. Slice out just
        # the songs column's own character range (computed the same way
        # _render_layout does, rather than hardcoded, so this keeps
        # meaning what it says as the column split changes) to confirm
        # "ZEBRA" genuinely comes from the songs column's artist text, not
        # queue bleed-through.
        col_width_folders, col_width_songs, _ = player._compute_column_widths(80)
        songs_start = col_width_folders + 1
        songs_end = col_width_folders + col_width_songs
        songs_column_only = songs_row[songs_start:songs_end]
        self.assertIn("ZEBRA", songs_column_only)


class ColumnWidthTests(unittest.TestCase):
    """Issue #24: Spotify-like layout -- the songs column gets most of the
    width, folders/queue are narrow side rails, and no column collapses
    on a narrow terminal."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_songs_column_is_wider_than_folders_and_queue(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        folders, songs, queue = player._compute_column_widths(100)
        self.assertGreater(songs, folders)
        self.assertGreater(songs, queue)
        self.assertGreater(songs, folders + queue)

    def test_widths_sum_to_terminal_width(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        folders, songs, queue = player._compute_column_widths(100)
        self.assertEqual(folders + songs + queue, 100)

    def test_narrow_terminal_does_not_collapse_a_column(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        folders, songs, queue = player._compute_column_widths(20)
        self.assertGreaterEqual(folders, 1)
        self.assertGreaterEqual(songs, 1)
        self.assertGreaterEqual(queue, 1)

    def test_narrow_terminal_layout_does_not_raise(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._colors_ready = True
        stdscr = FakeStdscr(height=24, width=20)

        with patch("curses.ACS_VLINE", ord("|"), create=True):
            player._render_layout(stdscr)  # must not raise


class QueueRenderTests(unittest.TestCase):
    """Issue #1: queue rows must not be numbered."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_render_queue_has_no_numeric_prefix(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.queue_panel.update_queue(self.catalog.tracks[:5])
        stdscr = FakeStdscr()

        player._render_queue(stdscr, row=0, col=0, width=40, height=10)

        rendered_lines = [text for _row, _col, text, _attr in stdscr.calls]
        # Upcoming queue rows start at index 3 (after "Now Playing", current line, "Queue (N)")
        upcoming_lines = rendered_lines[3:]
        self.assertTrue(any("-" in line for line in upcoming_lines))
        for line in upcoming_lines:
            self.assertIsNone(re.match(r"^\s*\d+\.\s", line))


class SongsRenderTests(unittest.TestCase):
    """Issue #4: "Play Random" row must always render above the song list."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_random_row_stays_fixed_regardless_of_scroll(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
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
        self.assertEqual(row_no_scroll, 3)  # below the name/path/count header rows

    def test_render_songs_syncs_visible_lines_for_scroll_centering(self) -> None:
        """SongsPanel._update_scroll's centering needs the real viewport
        height, not a guess -- _render_songs must publish it each tick."""
        player = Player3Column(self.library, self.settings, self.backend)
        player.songs_panel.songs = list(self.catalog.tracks) * 3

        stdscr = FakeStdscr()
        player._render_songs(stdscr, row=0, col=0, width=40, height=14)

        # height=14 minus the 4 header/button rows above the list = 10.
        self.assertEqual(player.songs_panel.visible_lines, 10)

    def test_song_row_shows_title_artist_and_duration(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        track = dataclasses.replace(
            self.catalog.tracks[0], title="Zebra Title", artist="Zeta Artist", duration=185.0,
        )
        player.songs_panel.songs = [track]
        player.songs_panel.selected_index = 0

        stdscr = FakeStdscr(width=60)
        player._render_songs(stdscr, row=0, col=0, width=60, height=10)

        song_row = next(text for _row, _col, text, _attr in stdscr.calls if "Zebra Title" in text)
        self.assertIn("Zeta Artist", song_row)
        self.assertIn("3:05", song_row)  # 185s = 3:05

    def test_song_row_shows_placeholder_when_duration_unknown(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        track = dataclasses.replace(
            self.catalog.tracks[0], title="No Duration Track", artist="Someone", duration=None,
        )
        player.songs_panel.songs = [track]
        player.songs_panel.selected_index = 0

        stdscr = FakeStdscr(width=60)
        player._render_songs(stdscr, row=0, col=0, width=60, height=10)

        song_row = next(text for _row, _col, text, _attr in stdscr.calls if "No Duration Track" in text)
        self.assertIn("--:--", song_row)

    def test_song_row_does_not_crash_on_narrow_width(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        track = dataclasses.replace(
            self.catalog.tracks[0],
            title="A Very Long Title That Will Not Fit",
            artist="A Very Long Artist Name",
            duration=185.0,
        )
        player.songs_panel.songs = [track]
        player.songs_panel.selected_index = 0

        stdscr = FakeStdscr(width=20)
        player._render_songs(stdscr, row=0, col=0, width=20, height=10)  # must not raise

        for _row, _col, text, _attr in stdscr.calls:
            self.assertLessEqual(len(text), 19)


class ColorAndFocusTests(unittest.TestCase):
    """Issue #2: real colors, and the cursor must show which column has focus."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_colors_not_initialized_on_construction(self) -> None:
        # curses.start_color()/init_pair() require a live curses screen, and
        # Player3Column is constructed directly (no curses.wrapper) in every
        # other test here, so color setup must stay lazy.
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertFalse(player._colors_ready)

    def test_cursor_attr_without_color_support_falls_back_to_legacy_attrs(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
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
        player = Player3Column(self.library, self.settings, self.backend)
        self._init_colors_without_real_screen(player)

        player.active_column = 0
        focused_attr = player._cursor_attr(0)
        unfocused_attr = player._cursor_attr(1)
        self.assertNotEqual(focused_attr, unfocused_attr)

    def test_render_folders_highlights_selected_row_only_when_focused(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        self._init_colors_without_real_screen(player)
        if len(player.folder_panel.entries) < 1:
            self.skipTest("no folders in test catalog")

        player.active_column = 0
        stdscr = FakeStdscr()
        player._render_folders(stdscr, row=0, col=0, width=20, height=10)
        focused_attrs = {attr for _r, _c, _t, attr in stdscr.calls}

        player.active_column = 1  # folders no longer focused
        stdscr = FakeStdscr()
        player._render_folders(stdscr, row=0, col=0, width=20, height=10)
        unfocused_attrs = {attr for _r, _c, _t, attr in stdscr.calls}

        self.assertNotEqual(focused_attrs, unfocused_attrs)


class ProgressBarTests(unittest.TestCase):
    """Issue #5: time progress bar and centered player controls."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_refresh_progress_updates_player_bar_from_backend(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        self.backend.get_time_pos.return_value = 61.0
        self.backend.get_duration.return_value = 180.0

        player._refresh_progress()

        self.assertEqual(player.player_bar.time_pos, 61.0)
        self.assertEqual(player.player_bar.duration, 180.0)

    def test_refresh_progress_noop_without_engine(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player.engine = None

        player._refresh_progress()

        self.backend.get_time_pos.assert_not_called()

    def test_render_player_bar_lines_are_centered(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
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
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_backward_compatible_construction_without_settings_path(self) -> None:
        # Locks in that settings_path stays optional/keyword-only so every
        # pre-existing 3-positional-argument call site keeps working.
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertEqual(player.mode, "player")

    def test_s_key_opens_settings_from_each_column(self) -> None:
        for column, handler_name in ((0, "handle_folder_input"), (1, "handle_songs_input")):
            with self.subTest(column=column):
                player = Player3Column(self.library, self.settings, self.backend)
                player.active_column = column
                handler = getattr(player, handler_name)
                result = handler(ord("s"))
                self.assertTrue(result)
                self.assertEqual(player.mode, "settings")
                self.assertEqual(player.settings_panel.top_k, self.settings.top_k)

    def test_settings_navigation_moves_field_index(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._enter_settings_mode()

        player.handle_settings_input(curses.KEY_DOWN)
        self.assertEqual(player.settings_panel.field_index, 1)
        player.handle_settings_input(curses.KEY_UP)
        self.assertEqual(player.settings_panel.field_index, 0)

    def test_settings_increment_adjusts_top_k(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._enter_settings_mode()
        initial = player.settings_panel.top_k

        player.handle_settings_input(ord("+"))

        self.assertEqual(player.settings_panel.top_k, initial + 1)

    def test_escape_cancels_without_saving(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._enter_settings_mode()
        player.handle_settings_input(ord("+"))

        with patch("src.player_ui_v2.save_settings") as mock_save:
            player.handle_settings_input(27)

        mock_save.assert_not_called()
        self.assertEqual(player.mode, "player")

    def test_apply_saves_settings_and_returns_to_player_mode(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._enter_settings_mode()
        player.handle_settings_input(ord("+"))  # top_k += 1
        expected = player.settings_panel.to_settings()

        with patch("src.player_ui_v2.save_settings") as mock_save:
            player.handle_settings_input(ord("a"))

        mock_save.assert_called_once_with(expected, player.settings_path)
        self.assertEqual(player.mode, "player")
        self.assertEqual(player.settings.top_k, expected.top_k)

    def test_quit_from_settings_mode_returns_false(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._enter_settings_mode()
        self.assertFalse(player.handle_input(ord("q")))

    def test_render_settings_does_not_raise_and_shows_all_fields(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._enter_settings_mode()
        stdscr = FakeStdscr(height=24, width=80)

        player._render_settings(stdscr, height=24, width=80)

        rendered = " ".join(text for _r, _c, text, _a in stdscr.calls)
        for field_name in ("top_k", "randomness", "queue_length", "library_path"):
            self.assertIn(field_name, rendered)


if __name__ == "__main__":
    unittest.main()


class BackgroundBpmAnalysisTests(unittest.TestCase):
    """Tempo detection runs on a background thread while the player is open,
    so the UI keeps rendering and playing through a run that takes tens of
    minutes. The thread never touches the catalog: it publishes results that
    the render loop drains and applies."""

    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)
        for track in self.catalog.tracks:
            track.bpm = None

    def _player(self) -> Player3Column:
        return Player3Column(self.library, self.settings, self.backend)

    def test_pressing_b_in_the_folder_column_starts_analysis(self) -> None:
        player = self._player()
        with patch("src.player_ui_v2.start_bpm_task") as start:
            player.handle_folder_input(ord("b"))

        start.assert_called_once()
        self.assertIn("Analyzing tempo", player.folder_panel.status_message)

    def test_only_tracks_without_a_tempo_are_submitted(self) -> None:
        self.catalog.tracks[0].bpm = 128.0
        player = self._player()
        with patch("src.player_ui_v2.start_bpm_task") as start:
            player.handle_folder_input(ord("b"))

        submitted = list(start.call_args[0][0])
        self.assertNotIn(self.catalog.tracks[0].id, [t.id for t in submitted])
        self.assertEqual(len(submitted), len(self.catalog.tracks) - 1)

    def test_pressing_b_again_cancels_a_running_analysis(self) -> None:
        player = self._player()
        task = MagicMock()
        with patch("src.player_ui_v2.start_bpm_task", return_value=task):
            player.handle_folder_input(ord("b"))
        player.handle_folder_input(ord("b"))

        task.cancel.assert_called_once()

    def test_nothing_to_do_is_reported_rather_than_starting_a_run(self) -> None:
        for track in self.catalog.tracks:
            track.bpm = 120.0
        player = self._player()
        with patch("src.player_ui_v2.start_bpm_task") as start:
            player.handle_folder_input(ord("b"))

        start.assert_not_called()
        self.assertIn("already have a tempo", player.folder_panel.status_message)

    def test_results_are_applied_and_invalidate_the_feature_cache(self) -> None:
        player = self._player()
        track = self.library.catalog.tracks[0]
        self.library.catalog.features_for(track)  # warm the cache
        self.assertIn(track.id, self.library.catalog.track_features)

        with patch.object(player, "_save_bpm_results"):
            player._apply_bpm_results({track.id: 128.0})

        self.assertEqual(track.bpm, 128.0)
        # Dropped rather than stale, so the next lookup re-derives with the
        # tempo included.
        self.assertNotIn(track.id, self.library.catalog.track_features)
        self.assertEqual(self.library.catalog.features_for(track).bpm, 128.0)

    def test_results_for_unknown_tracks_are_ignored(self) -> None:
        player = self._player()
        with patch.object(player, "_save_bpm_results"):
            player._apply_bpm_results({"no-such-track": 128.0})  # must not raise

    def test_progress_is_reported_while_running(self) -> None:
        player = self._player()
        task = MagicMock()
        task.done.is_set.return_value = False
        task.drain.return_value = {}
        task.progress.return_value = (7, 40)
        with patch("src.player_ui_v2.start_bpm_task", return_value=task):
            player.handle_folder_input(ord("b"))

        player._poll_bpm_task()

        self.assertEqual(player.folder_panel.status_message, "Analyzing tempo: 7/40")

    def test_partial_results_are_applied_before_the_run_finishes(self) -> None:
        # A run over thousands of files should not withhold everything until
        # it completes; an interrupted session keeps what it found.
        player = self._player()
        track = self.library.catalog.tracks[0]
        task = MagicMock()
        task.done.is_set.return_value = False
        task.drain.return_value = {track.id: 96.0}
        task.progress.return_value = (1, 40)
        with patch("src.player_ui_v2.start_bpm_task", return_value=task):
            player.handle_folder_input(ord("b"))

        player._poll_bpm_task()

        self.assertEqual(track.bpm, 96.0)

    def test_finishing_saves_the_library_and_reports_a_summary(self) -> None:
        player = self._player()
        task = MagicMock()
        task.done.is_set.return_value = True
        task.drain.return_value = {}
        task.progress.return_value = (40, 40)
        task.failed_count.return_value = 3
        task.cancelled.is_set.return_value = False
        task.error = []
        with patch("src.player_ui_v2.start_bpm_task", return_value=task):
            player.handle_folder_input(ord("b"))

        with patch("src.player_ui_v2.save_library") as save:
            player._poll_bpm_task()

        save.assert_called_once()
        self.assertIsNone(player._bpm_task)
        self.assertIn("37 detected", player.folder_panel.status_message)
        self.assertIn("3 undetectable", player.folder_panel.status_message)

    def test_quitting_keeps_results_detected_since_the_last_save(self) -> None:
        # The worker is a daemon thread, so without this the tempi found
        # since the last checkpoint would be discarded on exit.
        player = self._player()
        track = self.library.catalog.tracks[0]
        task = MagicMock()
        task.drain.return_value = {track.id: 101.0}
        with patch("src.player_ui_v2.start_bpm_task", return_value=task):
            player.handle_folder_input(ord("b"))

        with patch("src.player_ui_v2.save_library") as save:
            player._shutdown_bpm_task()

        task.cancel.assert_called_once()
        self.assertEqual(track.bpm, 101.0)
        save.assert_called_once()

    def test_adding_a_folder_is_refused_while_analysis_runs(self) -> None:
        # add_folder rebuilds the catalog and replaces self.library, which
        # would strand the results still being produced for the old one.
        player = self._player()
        with patch("src.player_ui_v2.start_bpm_task", return_value=MagicMock()):
            player.handle_folder_input(ord("b"))

        player.handle_folder_input(ord("a"))

        self.assertFalse(player._adding_folder)
        self.assertIn("Tempo analysis in progress", player.folder_panel.status_message)

    def test_playback_controls_still_work_during_analysis(self) -> None:
        player = self._player()
        with patch("src.player_ui_v2.start_bpm_task", return_value=MagicMock()):
            player.handle_folder_input(ord("b"))

        self.assertTrue(player.handle_folder_input(ord(" ")))
        self.assertFalse(player.handle_folder_input(ord("q")))
