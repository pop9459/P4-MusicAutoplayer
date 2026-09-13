"""Tests for the MPRIS action-queue/state plumbing and its wiring into
Player3Column._poll_mpris_task.

No real D-Bus test harness is used here: CI/sandbox D-Bus availability is
unpredictable even though this dev machine has a session bus. Instead we
unit-test the thread-safe data structures directly, and drive
_poll_mpris_task with an MprisService that was constructed but never
`start()`-ed (no real bus connection), pushing actions onto its queue by
hand -- exactly like a D-Bus method call would, but without touching a real
bus."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.library import Library, LibraryFolder
from src.mpris_service import MprisActionQueue, MprisService, MprisState
from src.mpv_backend import MpvBackend
from src.player_ui_v2 import Player3Column
from src.settings import load_settings
from src.track_analyzer import load_catalog


def _library_from_catalog(catalog):
    for track in catalog.tracks:
        track.folder_id = "testtracks"
    folder = LibraryFolder(
        id="testtracks", path="testTracks", display_name="testTracks",
        added_at="t", last_scanned_at="t", track_count=len(catalog.tracks),
    )
    return Library(version=1, folders=[folder], catalog=catalog)


class MprisActionQueueTests(unittest.TestCase):
    def test_drain_returns_pushed_actions_in_order(self) -> None:
        queue = MprisActionQueue()
        queue.push("play")
        queue.push("next")

        self.assertEqual(queue.drain(), ["play", "next"])

    def test_drain_empties_the_queue(self) -> None:
        queue = MprisActionQueue()
        queue.push("stop")
        queue.drain()

        self.assertEqual(queue.drain(), [])


class MprisStateTests(unittest.TestCase):
    def test_update_and_snapshot_round_trip(self) -> None:
        state = MprisState()
        state.update(playing=True, has_track=True, title="Title", artist="Artist", track_id="abc")

        snapshot = state.snapshot()

        self.assertTrue(snapshot.playing)
        self.assertTrue(snapshot.has_track)
        self.assertEqual(snapshot.title, "Title")
        self.assertEqual(snapshot.artist, "Artist")
        self.assertEqual(snapshot.track_id, "abc")

    def test_snapshot_is_a_copy(self) -> None:
        state = MprisState()
        snapshot = state.snapshot()
        state.update(playing=True, has_track=True, title="Changed", artist="A", track_id="x")

        self.assertEqual(snapshot.title, "")


class PollMprisTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(Path("testTracks/catalog.json"))
        self.library = _library_from_catalog(self.catalog)
        self.settings = load_settings()
        self.backend = MagicMock(spec=MpvBackend)

    def test_mpris_service_not_started_on_construction(self) -> None:
        """Player3Column construction must never start the D-Bus service --
        dozens of tests construct it directly with no real curses/D-Bus, and
        starting it there would risk hangs/crashes in headless test runs."""
        player = Player3Column(self.library, self.settings, self.backend)
        self.assertIsNone(player._mpris_service)

    def test_poll_mpris_task_noop_without_service(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._poll_mpris_task()  # must not raise

    def test_play_pause_action_toggles_pause(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._mpris_service = MprisService()
        self.backend.toggle_pause.return_value = True

        player._mpris_service.actions.push("play_pause")
        player._poll_mpris_task()

        self.backend.toggle_pause.assert_called_once()
        self.assertTrue(player.player_bar.paused)

    def test_play_action_is_noop_when_already_playing(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._mpris_service = MprisService()

        player._mpris_service.actions.push("play")
        player._poll_mpris_task()

        self.backend.toggle_pause.assert_not_called()

    def test_pause_action_pauses_when_playing(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._mpris_service = MprisService()
        self.backend.toggle_pause.return_value = True

        player._mpris_service.actions.push("pause")
        player._poll_mpris_task()

        self.backend.toggle_pause.assert_called_once()
        self.assertTrue(player.player_bar.paused)

    def test_next_action_advances_track(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._queue_task.done.wait(timeout=5)
        player._mpris_service = MprisService()
        initial_track = player.engine.current_track

        player._mpris_service.actions.push("next")
        player._poll_mpris_task()

        self.assertNotEqual(player.engine.current_track.id, initial_track.id)

    def test_stop_action_calls_backend_stop(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._mpris_service = MprisService()

        player._mpris_service.actions.push("stop")
        player._poll_mpris_task()

        self.backend.stop.assert_called_once()

    def test_poll_mpris_task_refreshes_state_mirror(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._play_selected_song()
        player._mpris_service = MprisService()

        player._poll_mpris_task()

        state = player._mpris_service.state.snapshot()
        self.assertTrue(state.has_track)
        self.assertEqual(state.title, player.engine.current_track.title)
        self.assertEqual(state.artist, player.engine.current_track.artist)

    def test_poll_mpris_task_state_mirror_without_engine(self) -> None:
        player = Player3Column(self.library, self.settings, self.backend)
        player._mpris_service = MprisService()

        player._poll_mpris_task()

        state = player._mpris_service.state.snapshot()
        self.assertFalse(state.has_track)
        self.assertFalse(state.playing)


if __name__ == "__main__":
    unittest.main()
