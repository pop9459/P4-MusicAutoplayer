"""New tabbed TUI player integrating UI manager, views, and player engine."""
from __future__ import annotations

import curses
import random
from pathlib import Path

from .mpv_backend import MpvBackend, MpvUnavailableError
from .player import PlayerEngine, filter_enabled_tracks
from .predictor import generate_queue
from .settings import Settings, save_settings
from .track_analyzer import Catalog, TrackRecord, build_catalog, load_catalog, save_catalog, scan_library
from .tui_views import draw_folders_tab, draw_player_tab, draw_queue_tab, draw_settings_tab, draw_tab_header
from .ui_manager import TAB_FOLDERS, TAB_PLAYER, TAB_QUEUE, TAB_SETTINGS, UIManager, UIState

POLL_INTERVAL_MS = 200


class TabletedPlayer:
    """Coordinates the tabbed TUI player with catalog, settings, and playback."""

    def __init__(
        self,
        catalog: Catalog,
        settings: Settings,
        start_track: TrackRecord,
        backend: MpvBackend,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.backend = backend
        self.engine = PlayerEngine(
            catalog,
            start_track,
            top_k=settings.top_k,
            randomness=settings.randomness,
            queue_length=settings.queue_length,
            max_consecutive_same_artist=settings.max_consecutive_same_artist,
        )
        self.ui_manager = UIManager()

        # Settings edit state
        self.folders_edit_field = str(settings.music_directory)
        self.settings_edit_values = [str(settings.top_k), f"{settings.randomness:.2f}", str(settings.queue_length)]
        self.settings_edit_field_index = 0

        self.backend.load_file(self.engine.current_track.path)

    def _advance_and_load(self) -> bool:
        """Advance playback and load the next track."""
        next_track = self.engine.advance()
        if next_track is None:
            self.ui_manager.state.status_message = "Queue exhausted -- no eligible tracks remain."
            return False
        self.backend.load_file(next_track.path)
        self.ui_manager.state.paused = False
        self.ui_manager.state.status_message = (
            "Queue regenerated." if self.engine.queue_regenerated else ""
        )
        return True

    def _rescan_catalog(self) -> None:
        """Rescan the music directory and rebuild the catalog."""
        try:
            self.ui_manager.state.status_message = "Scanning library..."
            new_catalog = build_catalog(scan_library(self.settings.music_directory))
            save_catalog(new_catalog, self.settings.catalog_path)
            self.catalog = new_catalog
            self.ui_manager.state.status_message = "Catalog updated."
        except Exception as error:
            self.ui_manager.state.status_message = f"Scan failed: {str(error)}"

    def run_player_tab(self, stdscr: curses._CursesWindow) -> bool:
        """Handle player tab logic and rendering. Returns False to quit."""
        draw_player_tab(
            stdscr,
            self.engine,
            self.ui_manager.state.paused,
            self.ui_manager.state.status_message,
        )

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return False
        elif key == ord("\t"):
            self.ui_manager.state.next_tab()
            self.ui_manager.state.status_message = ""
        elif key in (ord("p"), ord("P"), ord(" ")):
            self.ui_manager.state.paused = self.backend.toggle_pause()
            self.ui_manager.state.status_message = ""
        elif key in (ord("n"), ord("N")):
            if not self._advance_and_load():
                pass  # Status already set
        elif key in (ord("l"), ord("L")):
            self.ui_manager.state.set_tab(TAB_QUEUE)
        elif key in (ord("s"), ord("S")):
            self.ui_manager.state.set_tab(TAB_SETTINGS)
        elif key == -1 and not self.ui_manager.state.paused and self.backend.is_finished():
            if not self._advance_and_load():
                return False

        return True

    def run_queue_tab(self, stdscr: curses._CursesWindow) -> bool:
        """Handle queue tab logic and rendering. Returns False to quit."""
        draw_queue_tab(stdscr, self.engine)

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return False
        elif key == ord("\t"):
            self.ui_manager.state.next_tab()
            self.ui_manager.state.status_message = ""
        elif key in (ord("p"), ord("P"), ord(" ")):
            self.ui_manager.state.paused = self.backend.toggle_pause()
        elif key in (ord("n"), ord("N")):
            if not self._advance_and_load():
                pass

        return True

    def run_folders_tab(self, stdscr: curses._CursesWindow) -> bool:
        """Handle folders tab logic and rendering. Returns False to quit."""
        draw_folders_tab(
            stdscr,
            str(self.settings.music_directory),
            self.ui_manager.state.folders_edit_mode,
            self.folders_edit_field,
            self.ui_manager.state.status_message,
        )

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return False
        elif key == ord("\t"):
            self.ui_manager.state.next_tab()
            self.ui_manager.state.status_message = ""
            self.ui_manager.state.folders_edit_mode = False
        elif key in (ord("e"), ord("E")) and not self.ui_manager.state.folders_edit_mode:
            # Enter edit mode
            self.ui_manager.state.folders_edit_mode = True
            self.folders_edit_field = str(self.settings.music_directory)
        elif self.ui_manager.state.folders_edit_mode:
            if key == 27:  # Escape
                self.ui_manager.state.folders_edit_mode = False
                self.ui_manager.state.status_message = ""
            elif key == ord("\n") or key == ord("\r"):
                # Confirm edit
                new_path = Path(self.folders_edit_field)
                if new_path.exists():
                    self.settings = self.settings.with_music_directory(new_path)
                    save_settings(self.settings)
                    self._rescan_catalog()
                    self.ui_manager.state.folders_edit_mode = False
                else:
                    self.ui_manager.state.status_message = "Path does not exist."
            elif key == curses.KEY_BACKSPACE or key == 127:
                self.folders_edit_field = self.folders_edit_field[:-1]
            elif 32 <= key <= 126:
                self.folders_edit_field += chr(key)

        return True

    def run_settings_tab(self, stdscr: curses._CursesWindow) -> bool:
        """Handle settings tab logic and rendering. Returns False to quit."""
        draw_settings_tab(
            stdscr,
            self.settings.top_k,
            self.settings.randomness,
            self.settings.queue_length,
            self.ui_manager.state.settings_edit_mode,
            self.settings_edit_field_index,
            self.settings_edit_values,
            self.ui_manager.state.status_message,
        )

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return False
        elif key == ord("\t"):
            self.ui_manager.state.next_tab()
            self.ui_manager.state.status_message = ""
            if self.ui_manager.state.settings_edit_mode:
                self._save_settings_edits()
            self.ui_manager.state.settings_edit_mode = False
        elif key in (ord("e"), ord("E")) and not self.ui_manager.state.settings_edit_mode:
            # Enter edit mode
            self.ui_manager.state.settings_edit_mode = True
            self.settings_edit_field_index = 0
        elif self.ui_manager.state.settings_edit_mode:
            if key == 27:  # Escape
                self.ui_manager.state.settings_edit_mode = False
                self.ui_manager.state.status_message = ""
                self.settings_edit_values = [
                    str(self.settings.top_k),
                    f"{self.settings.randomness:.2f}",
                    str(self.settings.queue_length),
                ]
            elif key == ord("\n") or key == ord("\r"):
                # Save edits
                if self._save_settings_edits():
                    self.ui_manager.state.settings_edit_mode = False
            elif key == curses.KEY_UP or key == ord("k"):
                self.settings_edit_field_index = (self.settings_edit_field_index - 1) % 3
            elif key == curses.KEY_DOWN or key == ord("j"):
                self.settings_edit_field_index = (self.settings_edit_field_index + 1) % 3
            elif key == curses.KEY_BACKSPACE or key == 127:
                self.settings_edit_values[self.settings_edit_field_index] = (
                    self.settings_edit_values[self.settings_edit_field_index][:-1]
                )
            elif 32 <= key <= 126:
                self.settings_edit_values[self.settings_edit_field_index] += chr(key)

        return True

    def _save_settings_edits(self) -> bool:
        """Validate and save settings edits. Returns True if successful."""
        try:
            new_top_k = int(self.settings_edit_values[0])
            new_randomness = float(self.settings_edit_values[1])
            new_queue_length = int(self.settings_edit_values[2])

            if new_top_k < 1:
                raise ValueError("top-k must be at least 1")
            if not 0.0 <= new_randomness <= 1.0:
                raise ValueError("randomness must be 0.0 to 1.0")
            if new_queue_length < 1:
                raise ValueError("queue length must be at least 1")

            # Create new settings and update engine
            from dataclasses import replace
            self.settings = replace(
                self.settings,
                top_k=new_top_k,
                randomness=new_randomness,
                queue_length=new_queue_length,
            )
            save_settings(self.settings)

            # Rebuild player engine with new settings
            self.engine = PlayerEngine(
                self.catalog,
                self.engine.current_track,
                top_k=new_top_k,
                randomness=new_randomness,
                queue_length=new_queue_length,
                rng=random.Random(),
                max_consecutive_same_artist=self.settings.max_consecutive_same_artist,
            )

            self.ui_manager.state.status_message = "Settings saved."
            return True
        except ValueError as error:
            self.ui_manager.state.status_message = f"Error: {str(error)}"
            return False

    def run_loop(self, stdscr: curses._CursesWindow) -> None:
        """Main event loop for the tabbed player."""
        stdscr.timeout(POLL_INTERVAL_MS)

        while True:
            # Render based on active tab
            if self.ui_manager.state.active_tab == TAB_PLAYER:
                if not self.run_player_tab(stdscr):
                    return
            elif self.ui_manager.state.active_tab == TAB_QUEUE:
                if not self.run_queue_tab(stdscr):
                    return
            elif self.ui_manager.state.active_tab == TAB_FOLDERS:
                if not self.run_folders_tab(stdscr):
                    return
            elif self.ui_manager.state.active_tab == TAB_SETTINGS:
                if not self.run_settings_tab(stdscr):
                    return


def run(
    catalog: Catalog,
    settings: Settings,
    top_k: int,
    randomness: float,
    queue_length: int,
    start_track_id: str | None = None,
) -> int:
    """Entry point for the new tabbed TUI player."""
    enabled_tracks = filter_enabled_tracks(catalog)
    if not enabled_tracks:
        print("No enabled tracks available in the catalog.")
        return 1

    start_track: TrackRecord | None = None
    if start_track_id is not None:
        start_track = next((track for track in enabled_tracks if track.id == start_track_id), None)
        if start_track is None:
            print(f"Track not found or not enabled: {start_track_id}")
            return 1

    try:
        backend = MpvBackend()
    except MpvUnavailableError as error:
        print(str(error))
        return 1

    try:
        def _main(stdscr: curses._CursesWindow) -> None:
            nonlocal start_track
            if start_track is None:
                from .player import pick_starting_track
                start_track = pick_starting_track(stdscr, enabled_tracks)
            if start_track is None:
                return

            player = TabletedPlayer(catalog, settings, start_track, backend)
            player.run_loop(stdscr)

        curses.wrapper(_main)
        return 0
    finally:
        backend.shutdown()
