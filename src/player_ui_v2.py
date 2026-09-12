"""3-column layout player with bottom player bar."""
from __future__ import annotations

import curses
import random
from pathlib import Path

from .folder_panel import FolderPanel
from .mpv_backend import MpvBackend, MpvUnavailableError
from .player import PlayerEngine, filter_enabled_tracks
from .player_bar import PlayerBar
from .predictor import generate_queue
from .queue_panel import QueuePanel
from .settings import Settings, load_settings
from .songs_panel import SongsPanel
from .track_analyzer import Catalog, TrackRecord

POLL_INTERVAL_MS = 200

# Color pair IDs. Initialized lazily in _init_colors() (only once a real
# curses screen is running) rather than in __init__, since Player3Column is
# constructed directly (no curses.wrapper) throughout the test suite.
COLOR_FOCUSED = 1
COLOR_UNFOCUSED = 2
COLOR_HEADER = 3
COLOR_QUEUE_HEAD = 4
COLOR_PLAYER_BAR = 5
COLOR_PROGRESS = 6


class Player3Column:
    """3-column layout: folders | songs | queue + bottom player bar."""

    def __init__(
        self,
        catalog: Catalog,
        settings: Settings,
        backend: MpvBackend,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.backend = backend

        self.folder_panel = FolderPanel()
        self.songs_panel = SongsPanel()
        self.queue_panel = QueuePanel()
        self.player_bar = PlayerBar()

        self.engine: PlayerEngine | None = None
        self.active_column = 0  # 0=folders, 1=songs, 2=queue

        self._colors_ready = False
        self._has_colors = False

        # Init folder panel
        self.folder_panel.load_folders_from_settings(settings)
        if self.folder_panel.selected_folder:
            self.songs_panel.load_songs_from_catalog(catalog)
            if self.songs_panel.songs:
                self._init_engine_with_song(self.songs_panel.songs[0])

    def _init_colors(self) -> None:
        """Set up color pairs. Only safe to call once a real curses screen
        is running (curses.start_color() errors without one), so this is
        invoked lazily from _render_layout rather than __init__."""
        self._has_colors = curses.has_colors()
        if self._has_colors:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(COLOR_FOCUSED, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(COLOR_UNFOCUSED, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(COLOR_HEADER, curses.COLOR_YELLOW, -1)
            curses.init_pair(COLOR_QUEUE_HEAD, curses.COLOR_GREEN, -1)
            curses.init_pair(COLOR_PLAYER_BAR, curses.COLOR_BLACK, curses.COLOR_GREEN)
            curses.init_pair(COLOR_PROGRESS, curses.COLOR_GREEN, -1)
        self._colors_ready = True

    def _cursor_attr(self, column: int) -> int:
        """Highlight attribute for a selected row in `column`, distinguishing
        the focused (active) column from an unfocused one so the user can
        always tell which column has keyboard focus."""
        focused = self.active_column == column
        if not self._has_colors:
            return curses.A_STANDOUT if focused else curses.A_REVERSE
        return (curses.color_pair(COLOR_FOCUSED) | curses.A_BOLD) if focused else curses.color_pair(COLOR_UNFOCUSED)

    def _header_attr(self, column: int) -> int:
        """Highlight attribute for a column header, accenting the focused column."""
        if self.active_column != column:
            return curses.A_BOLD
        if not self._has_colors:
            return curses.A_BOLD | curses.A_UNDERLINE
        return curses.color_pair(COLOR_HEADER) | curses.A_BOLD

    def _init_engine_with_song(self, track: TrackRecord) -> None:
        """Initialize player engine with starting track."""
        self.engine = PlayerEngine(
            self.catalog,
            track,
            top_k=self.settings.top_k,
            randomness=self.settings.randomness,
            queue_length=self.settings.queue_length,
        )
        self.player_bar.update_track(track)
        self.queue_panel.update_queue(self.engine.queue)
        self.backend.load_file(track.path)

    def _play_random_song(self) -> None:
        """Pick random song from current folder."""
        if not self.songs_panel.songs:
            self.player_bar.set_status("No songs.")
            return

        track = random.choice(self.songs_panel.songs)
        self._init_engine_with_song(track)
        self.player_bar.set_status(f"Random: {track.title}")

    def _play_selected_song(self) -> None:
        """Play currently selected song."""
        if not self.songs_panel.selected_song:
            self.player_bar.set_status("No song selected.")
            return

        self._init_engine_with_song(self.songs_panel.selected_song)
        self.player_bar.set_status(f"Playing: {self.songs_panel.selected_song.title}")

    def _advance_track(self) -> bool:
        """Advance to next track. Return False if queue exhausted."""
        if not self.engine:
            return False

        next_track = self.engine.advance()
        if next_track is None:
            self.player_bar.set_status("Queue exhausted.")
            return False

        self.player_bar.update_track(next_track)
        self.queue_panel.update_queue(self.engine.queue)
        self.player_bar.set_status(f"Playing: {next_track.title}")
        self.backend.load_file(next_track.path)
        return True

    def handle_folder_input(self, key: int) -> bool:
        """Handle input in folder column. Return False to quit."""
        if key in (ord("q"), ord("Q")):
            return False
        elif key == curses.KEY_DOWN or key == ord("j"):
            self.folder_panel.next_folder()
            # Load songs from new folder
            if self.folder_panel.selected_folder:
                self.songs_panel.load_songs_from_catalog(self.catalog)
        elif key == curses.KEY_UP or key == ord("k"):
            self.folder_panel.previous_folder()
            if self.folder_panel.selected_folder:
                self.songs_panel.load_songs_from_catalog(self.catalog)
        elif key == ord("\t"):
            self.active_column = 1
        elif key in (ord("\n"), ord(" ")):
            if self.songs_panel.songs:
                self._play_selected_song()

        return True

    def handle_songs_input(self, key: int) -> bool:
        """Handle input in songs column. Return False to quit."""
        if key in (ord("q"), ord("Q")):
            return False
        elif key == curses.KEY_DOWN or key == ord("j"):
            self.songs_panel.next_song()
        elif key == curses.KEY_UP or key == ord("k"):
            self.songs_panel.previous_song()
        elif key == ord("\t"):
            self.active_column = 2
        elif key in (ord("p"), ord("P")) or key == ord(" "):
            if self.engine:
                self.player_bar.set_paused(self.backend.toggle_pause())
        elif key in (ord("n"), ord("N")):
            self._advance_track()
        elif key in (ord("r"), ord("R")):
            self._play_random_song()
        elif key in (ord("\n"),):
            self._play_selected_song()

        return True

    def handle_queue_input(self, key: int) -> bool:
        """Handle input in queue column. Return False to quit."""
        if key in (ord("q"), ord("Q")):
            return False
        elif key == curses.KEY_DOWN or key == ord("j"):
            self.queue_panel.scroll_down()
        elif key == curses.KEY_UP or key == ord("k"):
            self.queue_panel.scroll_up()
        elif key == ord("\t"):
            self.active_column = 0
        elif key in (ord("n"), ord("N")):
            self._advance_track()
        elif key in (ord("p"), ord("P")) or key == ord(" "):
            if self.engine:
                self.player_bar.set_paused(self.backend.toggle_pause())

        return True

    def handle_input(self, key: int) -> bool:
        """Dispatch key to active column. Return False to quit."""
        if self.active_column == 0:
            return self.handle_folder_input(key)
        elif self.active_column == 1:
            return self.handle_songs_input(key)
        else:
            return self.handle_queue_input(key)

    def run_loop(self, stdscr: curses._CursesWindow) -> None:
        """Main event loop."""
        stdscr.timeout(POLL_INTERVAL_MS)

        while True:
            # Render all panels
            self._render_layout(stdscr)

            # Handle input or auto-advance
            key = stdscr.getch()
            if key == -1 and self.engine and not self.player_bar.paused and self.backend.is_finished():
                if not self._advance_track():
                    return
            elif key != -1:
                if not self.handle_input(key):
                    return

    def _render_layout(self, stdscr: curses._CursesWindow) -> None:
        """Render full 3-column layout."""
        if not self._colors_ready:
            self._init_colors()

        stdscr.erase()
        height, width = stdscr.getmaxyx()

        # Columns: 20% folders, 40% songs, 40% queue
        col_width_folders = width // 5
        col_width_songs = (width * 2) // 5
        col_width_queue = width - col_width_folders - col_width_songs

        # Player bar takes bottom 2 lines
        content_height = height - 3

        # Render folders
        self._render_folders(stdscr, 0, 0, col_width_folders, content_height)

        # Render songs
        self._render_songs(stdscr, 0, col_width_folders, col_width_songs, content_height)

        # Render queue
        self._render_queue(stdscr, 0, col_width_folders + col_width_songs, col_width_queue, content_height)

        # Dividing lines
        for y in range(content_height):
            stdscr.addch(y, col_width_folders, curses.ACS_VLINE)
            stdscr.addch(y, col_width_folders + col_width_songs, curses.ACS_VLINE)

        # Player bar
        self._render_player_bar(stdscr, height - 2, width)

        stdscr.refresh()

    def _render_folders(self, stdscr: curses._CursesWindow, row: int, col: int, width: int, height: int) -> None:
        """Render folder list."""
        stdscr.addnstr(row, col, "Folders".ljust(width - 1), width - 1, self._header_attr(0))
        row += 1

        for i, folder in enumerate(self.folder_panel.folders):
            attr = self._cursor_attr(0) if i == self.folder_panel.selected_index else curses.A_NORMAL
            name = folder.name
            stdscr.addnstr(row, col, f"  {name}".ljust(width - 1)[:width - 1], width - 1, attr)
            row += 1
            if row >= height:
                break

    def _render_songs(self, stdscr: curses._CursesWindow, row: int, col: int, width: int, height: int) -> None:
        """Render song list."""
        stdscr.addnstr(row, col, "Songs".ljust(width - 1), width - 1, self._header_attr(1))
        row += 1

        # Random button: fixed row, always visible above the scrollable list.
        btn_attr = curses.A_BOLD if self.active_column == 1 else curses.A_NORMAL
        stdscr.addnstr(row, col, "[R] Play Random".ljust(width - 1)[:width - 1], width - 1, btn_attr)
        row += 1

        for track, idx, is_selected in self.songs_panel.get_visible_songs(height - 3):
            attr = self._cursor_attr(1) if is_selected else curses.A_NORMAL
            line = f"{idx + 1}. {track.title}"[:width - 1]
            stdscr.addnstr(row, col, line.ljust(width - 1), width - 1, attr)
            row += 1

    def _render_queue(self, stdscr: curses._CursesWindow, row: int, col: int, width: int, height: int) -> None:
        """Render upcoming queue."""
        stdscr.addnstr(row, col, f"Queue ({len(self.queue_panel.queue)})".ljust(width - 1)[:width - 1], width - 1, self._header_attr(2))
        row += 1

        head_attr = (curses.color_pair(COLOR_QUEUE_HEAD) | curses.A_BOLD) if self._has_colors else curses.A_BOLD
        for track, _, is_first in self.queue_panel.get_visible_queue(height - 2):
            attr = head_attr if is_first else curses.A_NORMAL
            line = f"{track.artist} - {track.title}"[:width - 1]
            stdscr.addnstr(row, col, line.ljust(width - 1), width - 1, attr)
            row += 1

    def _render_player_bar(self, stdscr: curses._CursesWindow, row: int, width: int) -> None:
        """Render bottom player bar."""
        state = self.player_bar.get_state_display()
        track_display = self.player_bar.get_track_display()
        controls = "[Space]Play/Pause  [N]ext  [R]andom  [Q]uit"

        bar_attr = curses.color_pair(COLOR_PLAYER_BAR) if self._has_colors else curses.A_REVERSE
        line = f"[{state}] {track_display} | {controls}"[:width - 1]
        stdscr.addnstr(row, 0, line.ljust(width - 1), width - 1, bar_attr)

        if self.player_bar.status_message:
            stdscr.addnstr(row + 1, 0, self.player_bar.status_message.ljust(width - 1)[:width - 1], width - 1, curses.A_DIM)


def run(
    catalog: Catalog,
    settings: Settings,
) -> int:
    """Entry point for 3-column player."""
    try:
        backend = MpvBackend()
    except MpvUnavailableError as error:
        print(str(error))
        return 1

    try:
        def _main(stdscr: curses._CursesWindow) -> None:
            player = Player3Column(catalog, settings, backend)
            player.run_loop(stdscr)

        curses.wrapper(_main)
        return 0
    finally:
        backend.shutdown()
