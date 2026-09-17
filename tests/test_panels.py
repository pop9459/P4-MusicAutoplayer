"""Tests for v2 UI panels: folder_panel, songs_panel, queue_panel, player_bar."""
from __future__ import annotations

import unittest
from pathlib import Path

from src.folder_panel import FolderEntry, FolderPanel
from src.library import ALL_TRACKS_FOLDER_ID, Library, LibraryFolder
from src.player_bar import PlayerBar
from src.queue_panel import QueuePanel
from src.settings import Settings, load_settings
from src.songs_panel import SongsPanel, _normalize_for_search
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


class NormalizeForSearchTests(unittest.TestCase):
    def test_strips_diacritics(self) -> None:
        self.assertEqual(_normalize_for_search("á é í ó ú ñ ü"), "a e i o u n u")

    def test_lowercases(self) -> None:
        self.assertEqual(_normalize_for_search("HELLO"), "hello")

    def test_plain_ascii_is_unchanged_besides_case(self) -> None:
        self.assertEqual(_normalize_for_search("Rock N Roll"), "rock n roll")


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

    def test_apply_filter_matches_title_case_insensitive(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.apply_filter("a1")
        self.assertEqual([track.id for track in panel.songs], ["a1"])

    def test_apply_filter_matches_artist_case_insensitive(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.apply_filter("ARTIST A")
        self.assertEqual([track.id for track in panel.songs], ["a1", "a2"])

    def test_apply_filter_plain_query_matches_accented_title(self) -> None:
        panel = SongsPanel()
        panel.all_songs = [
            TrackRecord(id="1", path="/m/1.mp3", title="Nezastavís", artist="4D"),
        ]
        panel.songs = list(panel.all_songs)
        panel.apply_filter("nezastavis")
        self.assertEqual([t.id for t in panel.songs], ["1"])

    def test_apply_filter_accented_query_matches_plain_title(self) -> None:
        panel = SongsPanel()
        panel.all_songs = [
            TrackRecord(id="1", path="/m/1.mp3", title="Nezastavis", artist="4D"),
        ]
        panel.songs = list(panel.all_songs)
        panel.apply_filter("nezastavís")
        self.assertEqual([t.id for t in panel.songs], ["1"])

    def test_apply_filter_empty_query_clears_filter(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.apply_filter("a1")
        panel.apply_filter("")
        self.assertEqual([track.id for track in panel.songs], ["a1", "a2", "b1"])
        self.assertEqual(panel.filter_query, "")

    def test_apply_filter_resets_selection_and_scroll(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.next_song()
        panel.scroll_offset = 2
        panel.apply_filter("b1")
        self.assertEqual(panel.selected_index, 0)
        self.assertEqual(panel.selected_song.id, "b1")
        self.assertEqual(panel.scroll_offset, 0)

    def test_apply_filter_does_not_mutate_all_songs(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        all_before = list(panel.all_songs)
        panel.apply_filter("a1")
        self.assertEqual(panel.all_songs, all_before)

    def test_apply_filter_no_match_yields_empty_list(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.apply_filter("nonexistent")
        self.assertEqual(panel.songs, [])
        self.assertIsNone(panel.selected_song)

    def test_load_songs_from_library_resets_filter(self) -> None:
        panel = SongsPanel()
        panel.load_songs_from_library(self.library, self._all_tracks_entry())
        panel.apply_filter("a1")
        panel.load_songs_from_library(self.library, self._folder_b_entry())
        self.assertEqual(panel.filter_query, "")
        self.assertEqual([track.id for track in panel.songs], ["b1"])

    def _sortable_tracks(self) -> list[TrackRecord]:
        """Three tracks whose title/artist/album orderings all disagree
        with each other and with declaration order, so each sort mode
        produces a distinguishable, verifiable result."""
        return [
            TrackRecord(id="1", path="/m/1.mp3", title="Zebra Song", artist="Alpha Artist", album="Middle Album"),
            TrackRecord(id="2", path="/m/2.mp3", title="Apple Song", artist="Zulu Artist", album="Alpha Album"),
            TrackRecord(id="3", path="/m/3.mp3", title="Mango Song", artist="Mid Artist", album="Zulu Album"),
        ]

    def _sortable_panel(self) -> SongsPanel:
        panel = SongsPanel()
        tracks = self._sortable_tracks()
        panel.all_songs = tracks
        panel.songs = list(tracks)
        panel.selected_index = 0
        panel.selected_song = tracks[0]
        return panel

    def test_cycle_sort_orders_by_title(self) -> None:
        panel = self._sortable_panel()
        panel.cycle_sort()
        self.assertEqual(panel.sort_mode, "title")
        self.assertEqual([t.id for t in panel.songs], ["2", "3", "1"])

    def test_cycle_sort_orders_by_artist(self) -> None:
        panel = self._sortable_panel()
        panel.cycle_sort()
        panel.cycle_sort()
        self.assertEqual(panel.sort_mode, "artist")
        self.assertEqual([t.id for t in panel.songs], ["1", "3", "2"])

    def test_cycle_sort_orders_by_album(self) -> None:
        panel = self._sortable_panel()
        panel.cycle_sort()
        panel.cycle_sort()
        panel.cycle_sort()
        self.assertEqual(panel.sort_mode, "album")
        self.assertEqual([t.id for t in panel.songs], ["2", "1", "3"])

    def test_cycle_sort_wraps_back_to_default(self) -> None:
        panel = self._sortable_panel()
        for _ in range(4):
            panel.cycle_sort()
        self.assertEqual(panel.sort_mode, "default")
        self.assertEqual([t.id for t in panel.songs], ["1", "2", "3"])

    def test_cycle_sort_preserves_selected_track_identity_across_reorder(self) -> None:
        panel = self._sortable_panel()
        panel.select_song(0)  # track "1" ("Zebra Song")
        panel.cycle_sort()  # sort by title -> "1" moves to the last slot
        self.assertEqual(panel.selected_song.id, "1")
        self.assertEqual(panel.selected_index, 2)

    def test_filter_and_sort_compose(self) -> None:
        panel = self._sortable_panel()
        panel.filter_query = "Song"  # matches all three titles
        panel._apply_filter_and_sort()
        panel.cycle_sort()  # -> title
        self.assertEqual([t.id for t in panel.songs], ["2", "3", "1"])
        panel.apply_filter("Zebra")
        self.assertEqual([t.id for t in panel.songs], ["1"])
        self.assertEqual(panel.sort_mode, "title")


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
