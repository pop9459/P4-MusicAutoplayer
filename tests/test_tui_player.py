"""Unit tests for the new tabbed TUI player components."""
from __future__ import annotations

import unittest
from pathlib import Path

from src.ui_manager import UIManager, UIState, TAB_PLAYER, TAB_QUEUE, TAB_FOLDERS, TAB_SETTINGS
from src.track_analyzer import TrackRecord, build_catalog


class UIStateTests(unittest.TestCase):
    def test_tab_navigation_cycles(self) -> None:
        state = UIState()
        self.assertEqual(state.active_tab, TAB_PLAYER)

        state.next_tab()
        self.assertEqual(state.active_tab, TAB_QUEUE)

        state.next_tab()
        self.assertEqual(state.active_tab, TAB_FOLDERS)

        state.next_tab()
        self.assertEqual(state.active_tab, TAB_SETTINGS)

        state.next_tab()
        self.assertEqual(state.active_tab, TAB_PLAYER)  # Cycles back

    def test_previous_tab_cycles_backward(self) -> None:
        state = UIState()
        state.active_tab = TAB_PLAYER

        state.previous_tab()
        self.assertEqual(state.active_tab, TAB_SETTINGS)

        state.previous_tab()
        self.assertEqual(state.active_tab, TAB_FOLDERS)

    def test_set_tab_directly(self) -> None:
        state = UIState()
        state.set_tab(TAB_SETTINGS)
        self.assertEqual(state.active_tab, TAB_SETTINGS)

        state.set_tab("invalid")
        self.assertEqual(state.active_tab, TAB_SETTINGS)  # Unchanged


class UIManagerTests(unittest.TestCase):
    def test_register_and_dispatch_handler(self) -> None:
        manager = UIManager()
        called = {"count": 0}

        def handler(mgr: UIManager) -> None:
            called["count"] += 1

        manager.register_handler(TAB_PLAYER, ord("p"), handler)
        result = manager.dispatch_key(ord("p"))

        self.assertTrue(result)
        self.assertEqual(called["count"], 1)

    def test_tab_navigation_via_dispatch(self) -> None:
        manager = UIManager()
        self.assertEqual(manager.state.active_tab, TAB_PLAYER)

        manager.dispatch_key(ord("\t"))
        self.assertEqual(manager.state.active_tab, TAB_QUEUE)

    def test_quit_signal_returns_false(self) -> None:
        manager = UIManager()
        result = manager.dispatch_key(ord("q"))
        self.assertFalse(result)

    def test_unhandled_key_returns_false(self) -> None:
        manager = UIManager()
        result = manager.dispatch_key(ord("x"))
        self.assertFalse(result)


class TabletedPlayerSetupTests(unittest.TestCase):
    def test_tabletted_player_can_be_instantiated(self) -> None:
        """Verify TabletedPlayer can be created with valid inputs."""
        from src.tui_player import TabletedPlayer
        from src.mpv_backend import MpvBackend
        from src.settings import Settings
        from unittest.mock import MagicMock, patch

        # Create a simple catalog
        catalog = build_catalog([
            TrackRecord(id="track1", path="/music/track1.mp3", title="Track 1", artist="Artist A"),
            TrackRecord(id="track2", path="/music/track2.mp3", title="Track 2", artist="Artist B"),
        ])

        # Create settings
        settings = Settings(
            catalog_path=Path("catalog.json"),
            music_directory=Path("music"),
            music_folders=(Path("music"),),
            top_k=5,
            randomness=0.0,
            queue_length=10,
        )

        # Mock the mpv backend
        mock_backend = MagicMock()
        mock_backend.load_file = MagicMock()

        start_track = catalog.tracks[0]

        # Create TabletedPlayer
        player = TabletedPlayer(catalog, settings, start_track, mock_backend)

        # Verify initialization
        self.assertEqual(player.settings, settings)
        self.assertEqual(player.engine.current_track.id, "track1")
        self.assertEqual(player.ui_manager.state.active_tab, TAB_PLAYER)
        mock_backend.load_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
