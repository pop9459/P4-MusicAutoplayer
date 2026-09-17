"""3-column layout player with bottom player bar."""

from __future__ import annotations

import curses
import random
import shutil
from pathlib import Path

from .bpm_analyzer import BpmTask, start_bpm_task, tracks_needing_bpm
from .folder_panel import FolderPanel
from .library import (
    Library,
    ScanTask,
    find_folder_by_path,
    load_library,
    save_library,
    start_add_folder_task,
)
from .mpris_service import MprisService, start_mpris_service
from .mpv_backend import MpvBackend, MpvUnavailableError
from .player import PlayerEngine, QueueTask, filter_enabled_tracks, start_queue_task
from .player_bar import PlayerBar
from .queue_panel import QueuePanel
from .settings import DEFAULT_SETTINGS_PATH, Settings, load_settings, save_settings
from .settings_panel import FIELDS as SETTINGS_FIELDS
from .settings_panel import SettingsPanel
from .songs_panel import SongsPanel
from .track_analyzer import Catalog, TrackRecord

POLL_INTERVAL_MS = 200

# Rows the terminal height loses before it becomes the queue's visible-row
# budget: _render_layout reserves 1 row for the top search bar and 4 for the
# player bar, then _render_queue reserves 3 more rows for its own header and
# "Now Playing" line -- so the queue column can show
# height - 1 - 4 - 3 = height - 8 rows before needing to scroll.
QUEUE_COLUMN_CHROME_ROWS = 8

SEARCH_BAR_ROWS = 1

MIN_QUEUE_LENGTH = 10

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
        library: Library,
        settings: Settings,
        backend: MpvBackend,
        *,
        settings_path: Path = DEFAULT_SETTINGS_PATH,
    ) -> None:
        self.library = library
        self.settings = settings
        self.settings_path = settings_path
        self.backend = backend

        self.folder_panel = FolderPanel()
        self.songs_panel = SongsPanel()
        self.queue_panel = QueuePanel()
        self.player_bar = PlayerBar()
        self.settings_panel = SettingsPanel()

        self.engine: PlayerEngine | None = None
        self.active_column = 0  # 0=folders, 1=songs
        self.mode = "player"  # "player" | "settings"

        self._colors_ready = False
        self._has_colors = False

        self._scan_task: ScanTask | None = None
        self._adding_folder = False
        self._search_mode = False

        self._bpm_task: BpmTask | None = None
        self._bpm_applied_since_save = 0

        self._queue_task: QueueTask | None = None
        self._queue_task_last_revealed = -1

        # Not started here: MPRIS needs a real event loop/D-Bus session, and
        # Player3Column is constructed directly (no curses/D-Bus) throughout
        # the test suite. Started from run_loop instead, once curses is live.
        self._mpris_service: MprisService | None = None

        # Set once run_loop starts (None beforehand, e.g. during this
        # __init__'s own initial engine setup below, before curses exists).
        self._stdscr: curses._CursesWindow | None = None
        self._term_height: int | None = None

        # Init folder panel
        self.folder_panel.load_from_library(library)
        if self.folder_panel.selected_entry:
            self.songs_panel.load_songs_from_library(
                self.library, self.folder_panel.selected_entry
            )

    @property
    def catalog(self) -> Catalog:
        return self.library.catalog

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
        return (
            (curses.color_pair(COLOR_FOCUSED) | curses.A_BOLD)
            if focused
            else curses.color_pair(COLOR_UNFOCUSED)
        )

    def _header_attr(self, column: int) -> int:
        """Highlight attribute for a column header, accenting the focused column."""
        if self.active_column != column:
            return curses.A_BOLD
        if not self._has_colors:
            return curses.A_BOLD | curses.A_UNDERLINE
        return curses.color_pair(COLOR_HEADER) | curses.A_BOLD

    def _effective_queue_length(self) -> int:
        """Queue length sized to fill the visible queue column, so it plays
        without needing to scroll -- floored at 10 and at the configured
        `settings.queue_length` (whichever of the three is largest wins).

        Uses the last known terminal height (updated each render tick in
        _render_layout); falls back to the real terminal size for the very
        first engine build in __init__, which runs before curses starts.
        """
        height = self._term_height or shutil.get_terminal_size().lines
        visible_rows = max(0, height - QUEUE_COLUMN_CHROME_ROWS)
        return max(MIN_QUEUE_LENGTH, self.settings.queue_length, visible_rows)

    def _begin_queue_task(self, steps_factory) -> None:
        """Start building/topping-up the queue on a background thread so
        queue generation's full-catalog similarity scan never blocks curses
        input. Cancels any still-running previous task first (e.g. rapid
        track advances), since only one task may safely mutate
        `engine.queue` at a time."""
        if self._queue_task is not None and not self._queue_task.done.is_set():
            self._queue_task.cancel.set()
        self._queue_task = start_queue_task(steps_factory)
        self._queue_task_last_revealed = -1

    def _poll_queue_task(self) -> None:
        """Check on a running queue-build/top-up task; reflect its progress
        in the queue panel each tick and clear it once done."""
        if self._queue_task is None or self.engine is None:
            return

        revealed = self._queue_task.revealed_count()
        if revealed != self._queue_task_last_revealed:
            self._queue_task_last_revealed = revealed
            self.queue_panel.update_queue(self.engine.queue_snapshot(), self.engine.current_track)

        if not self._queue_task.done.is_set():
            return

        task = self._queue_task
        self._queue_task = None
        if task.error:
            self.player_bar.set_status(f"Queue build failed: {task.error[0]}")

    def _poll_mpris_task(self) -> None:
        """Drain actions requested via MPRIS (hardware media keys) and apply
        them through the same methods a keypress would use, then refresh the
        state mirror MPRIS reads for PlaybackStatus/Metadata."""
        if self._mpris_service is None:
            return

        for action in self._mpris_service.actions.drain():
            if action == "play_pause":
                if self.engine:
                    self.player_bar.set_paused(self.backend.toggle_pause())
            elif action == "play":
                if self.engine and self.player_bar.paused:
                    self.player_bar.set_paused(self.backend.toggle_pause())
            elif action == "pause":
                if self.engine and not self.player_bar.paused:
                    self.player_bar.set_paused(self.backend.toggle_pause())
            elif action == "next":
                self._advance_track()
            elif action == "previous":
                self._go_back_track()
            elif action == "stop":
                if self.engine:
                    self.backend.stop()
                    self.player_bar.set_status("Stopped.")

        track = self.engine.current_track if self.engine else None
        self._mpris_service.state.update(
            playing=bool(self.engine) and not self.player_bar.paused,
            has_track=track is not None,
            title=track.title if track else "",
            artist=track.artist if track else "",
            track_id=track.id if track else "",
        )

    def _init_engine_with_song(self, track: TrackRecord) -> None:
        """Initialize player engine with starting track.

        Starts mpv playback before building the recommendation queue: queue
        generation does a full-catalog similarity scan per queue slot, which
        can take seconds on a large library, and there is no reason to make
        the user wait through that before hearing audio.
        """
        self.engine = PlayerEngine(
            self.catalog,
            track,
            top_k=self.settings.top_k,
            randomness=self.settings.randomness,
            queue_length=self._effective_queue_length(),
            max_consecutive_same_artist=self.settings.max_consecutive_same_artist,
            defer_queue=True,
        )
        self.player_bar.update_track(track)
        self.backend.load_file(track.path)
        self._begin_queue_task(self.engine.build_initial_queue_steps)

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
        """Advance to next track. Return False if queue exhausted.

        Loads the next track into mpv before topping the queue back up, for
        the same reason as `_init_engine_with_song`: playback shouldn't wait
        on a recommendation scan.
        """
        if not self.engine:
            return False

        if not self.engine.queue and self._queue_task is not None and not self._queue_task.done.is_set():
            # The initial queue build is still running in the background --
            # this isn't exhaustion, just not ready yet.
            self.player_bar.set_status("Queue still building...")
            return True

        next_track = self.engine.advance_immediate()
        if next_track is None:
            self.player_bar.set_status("Queue exhausted.")
            return False

        self.player_bar.update_track(next_track)
        self.player_bar.set_status(f"Playing: {next_track.title}")
        self.backend.load_file(next_track.path)
        self._begin_queue_task(self.engine.top_up_queue_steps)
        return True

    def _go_back_track(self) -> bool:
        """Move to the previous track in history. Unlike `_advance_track`,
        the queue doesn't shrink (the current track is bumped back onto its
        front), so no background top-up task is needed -- but the queue
        panel must be refreshed here explicitly, since it's otherwise only
        refreshed by `_poll_queue_task` when a top-up task is running."""
        if not self.engine:
            return False

        previous_track = self.engine.go_back()
        if previous_track is None:
            self.player_bar.set_status("No previous track.")
            return True

        self.player_bar.update_track(previous_track)
        self.player_bar.set_status(f"Playing: {previous_track.title}")
        self.backend.load_file(previous_track.path)
        self.queue_panel.update_queue(self.engine.queue_snapshot(), self.engine.current_track)
        return True

    def _handle_playback_keys(self, key: int) -> bool | None:
        """Playback controls usable from any column (folders or songs) --
        so play/pause etc. aren't only reachable once the songs column has
        focus. Returns False to quit, True if the key was consumed here, or
        None so the caller falls through to column-specific handling
        (navigation, Enter-to-play, etc.)."""
        if key in (ord("q"), ord("Q")):
            return False
        elif key in (ord("p"), ord("P")) or key == ord(" "):
            if self.engine:
                self.player_bar.set_paused(self.backend.toggle_pause())
            return True
        elif key in (ord("n"), ord("N")):
            self._advance_track()
            return True
        elif key == ord(","):
            self._go_back_track()
            return True
        elif key in (ord("r"), ord("R")):
            self._play_random_song()
            return True
        elif key in (ord("s"), ord("S")):
            self._enter_settings_mode()
            return True
        elif key == ord("/"):
            self._search_mode = True
            return True
        return None

    def handle_folder_input(self, key: int) -> bool:
        """Handle input in folder column. Return False to quit."""
        result = self._handle_playback_keys(key)
        if result is not None:
            return result

        if key == curses.KEY_DOWN or key == ord("j"):
            self.folder_panel.next_folder()
            if self.folder_panel.selected_entry:
                self.songs_panel.load_songs_from_library(
                    self.library, self.folder_panel.selected_entry
                )
        elif key == curses.KEY_UP or key == ord("k"):
            self.folder_panel.previous_folder()
            if self.folder_panel.selected_entry:
                self.songs_panel.load_songs_from_library(
                    self.library, self.folder_panel.selected_entry
                )
        elif key == ord("\t"):
            self.active_column = 1
        elif key == ord("\n"):
            self.active_column = 1
        elif key in (ord("a"), ord("A")):
            self._begin_add_folder_prompt()
        elif key in (ord("b"), ord("B")):
            self._toggle_bpm_analysis()

        return True

    def handle_songs_input(self, key: int) -> bool:
        """Handle input in songs column. Return False to quit."""
        result = self._handle_playback_keys(key)
        if result is not None:
            return result

        if key == curses.KEY_DOWN or key == ord("j"):
            self.songs_panel.next_song()
        elif key == curses.KEY_UP or key == ord("k"):
            self.songs_panel.previous_song()
        elif key == ord("\t"):
            self.active_column = 0
        elif key in (ord("\n"),):
            self._play_selected_song()

        return True

    def _enter_settings_mode(self) -> None:
        """Open the settings screen, loading a fresh editable copy of settings."""
        self.settings_panel.load_from_settings(self.settings)
        self.mode = "settings"

    def _begin_add_folder_prompt(self) -> None:
        """Open a blocking path prompt (handled by _edit_text_field_for_folder
        on the next run_loop tick) to add a new tracked folder. A path pasted
        by drag-and-drop lands in the same prompt as a typed one, since most
        terminals paste a dropped file's path as plain text."""
        if self._scan_task is not None:
            self.folder_panel.status_message = "Scan already in progress."
            return
        if self._bpm_task is not None:
            # Adding a folder rebuilds the merged catalog and replaces
            # self.library, which would strand the results the analysis
            # thread is still producing for the old one.
            self.folder_panel.status_message = "Tempo analysis in progress (B to stop)."
            return
        self._adding_folder = True

    def _begin_add_folder_scan(self, raw_path: str) -> None:
        raw_path = raw_path.strip()
        if not raw_path:
            return
        path = Path(raw_path).expanduser()
        existing = find_folder_by_path(self.library, path)
        if existing is not None:
            self.folder_panel.status_message = (
                f"Already tracked: {existing.display_name}"
            )
            return
        self.folder_panel.status_message = "Scanning: 0/0"
        self._scan_task = start_add_folder_task(self.library, path)

    # How many newly detected tempi to accumulate before writing the library
    # back out. Saving costs ~35ms on a 2600-track library, which is well
    # inside one 200ms render tick, but a run lasting tens of minutes
    # shouldn't rewrite the file after every single track either.
    _BPM_SAVE_EVERY = 100

    def _toggle_bpm_analysis(self) -> None:
        """Start tempo detection in the background, or stop a running one."""
        if self._bpm_task is not None:
            self._bpm_task.cancel()
            self.folder_panel.status_message = "Stopping tempo analysis..."
            return
        if self._scan_task is not None:
            self.folder_panel.status_message = "Scan in progress."
            return

        pending = tracks_needing_bpm(self.library.catalog.tracks)
        if not pending:
            self.folder_panel.status_message = "All tracks already have a tempo."
            return
        self.folder_panel.status_message = f"Analyzing tempo: 0/{len(pending)}"
        self._bpm_task = start_bpm_task(pending)

    def _apply_bpm_results(self, results: dict[str, float]) -> None:
        """Write detected tempi onto the live catalog, from the UI thread.

        Dropping each updated track's cached features is what makes the new
        tempo take effect: `Catalog.features_for` re-derives on the next
        lookup. Queue generation may be reading the catalog from its own
        thread meanwhile, but every value it can see is a valid one -- the
        worst case is one pick scored with a tempo that arrived a moment
        later.
        """
        if not results:
            return
        tracks_by_id = {track.id: track for track in self.library.catalog.tracks}
        for track_id, bpm in results.items():
            track = tracks_by_id.get(track_id)
            if track is None:
                continue
            track.bpm = bpm
            self.library.catalog.track_features.pop(track_id, None)
        self._bpm_applied_since_save += len(results)

    def _save_bpm_results(self) -> None:
        self._bpm_applied_since_save = 0
        try:
            save_library(self.library, self.settings.library_path)
        except OSError as error:
            self.folder_panel.status_message = f"Save failed: {error}"

    def _poll_bpm_task(self) -> None:
        """Apply whatever tempo analysis has finished since the last tick."""
        if self._bpm_task is None:
            return

        task = self._bpm_task
        self._apply_bpm_results(task.drain())

        if not task.done.is_set():
            analyzed, total = task.progress()
            self.folder_panel.status_message = f"Analyzing tempo: {analyzed}/{total}"
            if self._bpm_applied_since_save >= self._BPM_SAVE_EVERY:
                self._save_bpm_results()
            return

        self._bpm_task = None
        self._save_bpm_results()

        if task.error:
            self.folder_panel.status_message = f"Tempo analysis failed: {task.error[0]}"
            return

        analyzed, total = task.progress()
        failed = task.failed_count()
        detected = analyzed - failed
        if task.cancelled.is_set():
            self.folder_panel.status_message = f"Tempo analysis stopped ({detected} of {total} done)."
        elif failed:
            self.folder_panel.status_message = f"Tempo: {detected} detected, {failed} undetectable."
        else:
            self.folder_panel.status_message = f"Tempo analysis done ({detected} tracks)."

    def _shutdown_bpm_task(self) -> None:
        """Stop analysis and keep whatever it already found.

        The worker is a daemon thread, so quitting would otherwise discard
        everything detected since the last save -- up to a hundred tracks.
        """
        if self._bpm_task is None:
            return
        task = self._bpm_task
        self._bpm_task = None
        task.cancel()
        self._apply_bpm_results(task.drain())
        if self._bpm_applied_since_save:
            self._save_bpm_results()

    def _poll_scan_task(self) -> None:
        """Check on a running add-folder scan; apply its result once done."""
        if self._scan_task is None:
            return

        if not self._scan_task.done.is_set():
            scanned, total = self._scan_task.progress()
            self.folder_panel.status_message = f"Scanning: {scanned}/{total}"
            return

        task = self._scan_task
        self._scan_task = None

        if task.error:
            self.folder_panel.status_message = f"Add folder failed: {task.error[0]}"
            return

        new_library, folder, was_added = task.result[0]
        self.library = new_library
        try:
            save_library(self.library, self.settings.library_path)
        except OSError as error:
            self.folder_panel.status_message = f"Save failed: {error}"

        self.folder_panel.load_from_library(self.library)
        for index, entry in enumerate(self.folder_panel.entries):
            if entry.id == folder.id:
                self.folder_panel.select_folder(index)
                break
        if self.folder_panel.selected_entry:
            self.songs_panel.load_songs_from_library(
                self.library, self.folder_panel.selected_entry
            )
        message = (
            "Already tracked"
            if not was_added
            else f"Added {folder.display_name} ({folder.track_count} tracks)."
        )
        self.folder_panel.status_message = message

    def _apply_settings(self) -> None:
        """Persist the edited settings, then live-reapply them."""
        new_settings = self.settings_panel.to_settings()
        try:
            save_settings(new_settings, self.settings_path)
        except OSError as error:
            self.settings_panel.status_message = f"Save failed: {error}"
            return

        library_changed = new_settings.library_path != self.settings.library_path
        self.settings = new_settings

        if library_changed:
            try:
                self.library = load_library(self.settings.library_path)
            except (OSError, ValueError) as error:
                self.settings_panel.status_message = f"Library reload failed: {error}"

        self.folder_panel.load_from_library(self.library)
        if self.folder_panel.selected_entry:
            self.songs_panel.load_songs_from_library(
                self.library, self.folder_panel.selected_entry
            )

        if self.engine is not None:
            self._init_engine_with_song(self.engine.current_track)

        self.player_bar.set_status("Settings applied.")
        self.mode = "player"

    def _refresh_progress(self) -> None:
        """Poll the backend for the current playback position/duration."""
        if self.engine:
            self.player_bar.update_progress(
                self.backend.get_time_pos(), self.backend.get_duration()
            )

    def handle_input(self, key: int) -> bool:
        """Dispatch key to the search box if it's open, to the settings
        screen if open, or to the active column. Return False to quit."""
        if self._search_mode:
            return self._handle_search_input(key)
        if self.mode == "settings":
            return self.handle_settings_input(key)
        if self.active_column == 0:
            return self.handle_folder_input(key)
        else:
            return self.handle_songs_input(key)

    def _handle_search_input(self, key: int) -> bool:
        """Live, Spotify-style search: every keystroke re-filters the song
        list immediately (via SongsPanel.apply_filter), rather than
        blocking on a curses.echo()/getstr() prompt like the add-folder
        flow. The search box owns all input while open, so quit/navigation
        keys are swallowed rather than acted on -- typing "q" in a query
        must not exit the player."""
        if key == 27:  # Esc: discard the query, clear the filter, close the box.
            self._search_mode = False
            self.songs_panel.apply_filter("")
            return True
        if key in (curses.KEY_ENTER, 10, 13):  # Enter: keep the filter, close the box.
            self._search_mode = False
            return True
        if key in (curses.KEY_BACKSPACE, 127, 8):
            self.songs_panel.apply_filter(self.songs_panel.filter_query[:-1])
            return True
        if 32 <= key <= 126:  # printable ASCII
            self.songs_panel.apply_filter(self.songs_panel.filter_query + chr(key))
            return True
        return True

    def handle_settings_input(self, key: int) -> bool:
        """Handle input while the settings screen is open. Return False to quit."""
        if key in (ord("q"), ord("Q")):
            return False
        elif key == 27:  # Esc: discard edits, return to the player screen
            self.settings_panel.cancel_text_edit()
            self.mode = "player"
        elif key == curses.KEY_DOWN or key == ord("j"):
            self.settings_panel.next_field()
        elif key == curses.KEY_UP or key == ord("k"):
            self.settings_panel.previous_field()
        elif key in (curses.KEY_RIGHT, ord("+"), ord("l")):
            self.settings_panel.increment()
        elif key in (curses.KEY_LEFT, ord("-"), ord("h")):
            self.settings_panel.decrement()
        elif key == ord("\n"):
            self.settings_panel.begin_text_edit()
        elif key in (ord("a"), ord("A")):
            self._apply_settings()

        return True

    def run_loop(self, stdscr: curses._CursesWindow) -> None:
        """Main event loop."""
        self._stdscr = stdscr
        stdscr.timeout(POLL_INTERVAL_MS)
        self._mpris_service = start_mpris_service()

        try:
            self._event_loop(stdscr)
        finally:
            self._shutdown_bpm_task()

    def _event_loop(self, stdscr: curses._CursesWindow) -> None:
        while True:
            # Render all panels
            self._refresh_progress()
            self._poll_scan_task()
            self._poll_bpm_task()
            self._poll_queue_task()
            self._poll_mpris_task()
            self._render_layout(stdscr)

            # Handle input or auto-advance
            key = stdscr.getch()
            if (
                key == -1
                and self.engine
                and not self.player_bar.paused
                and self.backend.is_finished()
            ):
                if not self._advance_track():
                    return
            elif key != -1:
                if not self.handle_input(key):
                    return

            if self._adding_folder:
                self._adding_folder = False
                self._prompt_add_folder(stdscr)
            elif self.settings_panel.editing_text:
                self._edit_text_field(stdscr)

    def _prompt_add_folder(self, stdscr: curses._CursesWindow) -> None:
        """Synchronously prompt for a folder path to add (blocking
        curses.echo()/getstr(), same pattern as _edit_text_field)."""
        height, width = stdscr.getmaxyx()
        prompt = "Add folder path: "
        stdscr.addnstr(
            height - 1, 0, prompt.ljust(width - 1), width - 1, curses.A_REVERSE
        )
        stdscr.refresh()

        curses.echo()
        curses.curs_set(1)
        stdscr.timeout(-1)
        try:
            raw = stdscr.getstr(height - 1, len(prompt), width - len(prompt) - 1)
            value = raw.decode("utf-8", errors="replace")
        finally:
            curses.noecho()
            curses.curs_set(0)
            stdscr.timeout(POLL_INTERVAL_MS)

        self._begin_add_folder_scan(value)

    def _edit_text_field(self, stdscr: curses._CursesWindow) -> None:
        """Synchronously prompt for a new value for the field currently being
        text-edited (only src/settings_panel.py's `library_path` today).
        Needs `stdscr` directly (curses.echo()/getstr()), which is why this
        lives in run_loop's caller rather than handle_settings_input."""
        height, width = stdscr.getmaxyx()
        prompt = f"New {self.settings_panel.current_field()}: "
        stdscr.addnstr(
            height - 1, 0, prompt.ljust(width - 1), width - 1, curses.A_REVERSE
        )
        stdscr.refresh()

        curses.echo()
        curses.curs_set(1)
        stdscr.timeout(-1)
        try:
            raw = stdscr.getstr(height - 1, len(prompt), width - len(prompt) - 1)
            value = raw.decode("utf-8", errors="replace")
        finally:
            curses.noecho()
            curses.curs_set(0)
            stdscr.timeout(POLL_INTERVAL_MS)

        self.settings_panel.apply_text_edit(value)

    def _render_layout(self, stdscr: curses._CursesWindow) -> None:
        """Render full 3-column layout."""
        if not self._colors_ready:
            self._init_colors()

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        self._term_height = height

        if self.mode == "settings":
            self._render_settings(stdscr, height, width)
            stdscr.refresh()
            return

        # Columns: 20% folders, 40% songs, 40% queue
        col_width_folders = width // 5
        col_width_songs = (width * 2) // 5
        col_width_queue = width - col_width_folders - col_width_songs

        # Search bar takes the top line; player bar takes the bottom 4.
        content_top = SEARCH_BAR_ROWS
        content_bottom = height - 4

        self._render_search_bar(stdscr, 0, width)

        # Render folders
        self._render_folders(
            stdscr, content_top, 0, col_width_folders, content_bottom
        )

        # Render songs. Content starts one column right of the divider so
        # the divider doesn't overwrite the first character of each row.
        self._render_songs(
            stdscr, content_top, col_width_folders + 1, col_width_songs - 1, content_bottom
        )

        # Render queue, same one-column offset for the same reason.
        self._render_queue(
            stdscr,
            content_top,
            col_width_folders + col_width_songs + 1,
            col_width_queue - 1,
            content_bottom,
        )

        # Dividing lines
        for y in range(content_top, content_bottom):
            stdscr.addch(y, col_width_folders, curses.ACS_VLINE)
            stdscr.addch(y, col_width_folders + col_width_songs, curses.ACS_VLINE)

        # Player bar
        self._render_player_bar(stdscr, height - 3, width)

        stdscr.refresh()

    def _render_search_bar(self, stdscr: curses._CursesWindow, row: int, width: int) -> None:
        """Persistent top-of-screen search bar (Spotify-style): open with
        "/" from any column, live-filters the song list on every keystroke
        via _handle_search_input, closes with Enter (keeping the filter) or
        Esc (clearing it)."""
        if self._search_mode:
            text = f" Search: {self.songs_panel.filter_query}_"
            attr = curses.A_REVERSE
        elif self.songs_panel.filter_query:
            match_count = len(self.songs_panel.songs)
            text = (
                f" Search: {self.songs_panel.filter_query}"
                f"  ({match_count} match{'es' if match_count != 1 else ''})"
                "  [/] edit"
            )
            attr = curses.A_DIM
        else:
            text = " [/] Search tracks..."
            attr = curses.A_DIM

        stdscr.addnstr(row, 0, text.ljust(width - 1)[: width - 1], width - 1, attr)

    def _render_folders(
        self, stdscr: curses._CursesWindow, row: int, col: int, width: int, height: int
    ) -> None:
        """Render folder list (All Tracks + tracked folders)."""
        stdscr.addnstr(
            row, col, "Folders".ljust(width - 1), width - 1, self._header_attr(0)
        )
        row += 1

        for i, entry in enumerate(self.folder_panel.entries):
            attr = (
                self._cursor_attr(0)
                if i == self.folder_panel.selected_index
                else curses.A_NORMAL
            )
            stdscr.addnstr(
                row,
                col,
                f"  {entry.display_name}".ljust(width - 1)[: width - 1],
                width - 1,
                attr,
            )
            row += 1
            if row >= height - 1:
                break

        if self.folder_panel.status_message:
            stdscr.addnstr(
                height - 1,
                col,
                self.folder_panel.status_message[: width - 1],
                width - 1,
                curses.A_DIM,
            )
        elif row < height:
            stdscr.addnstr(
                height - 1,
                col,
                "[A] Add folder  [B] Analyze tempo".ljust(width - 1)[: width - 1],
                width - 1,
                curses.A_DIM,
            )

    def _render_songs(
        self, stdscr: curses._CursesWindow, row: int, col: int, width: int, height: int
    ) -> None:
        """Render selected-folder header (name/path/count) then the song list."""
        entry = self.folder_panel.selected_entry
        header = entry.display_name if entry else "Songs"
        stdscr.addnstr(
            row,
            col,
            header.ljust(width - 1)[: width - 1],
            width - 1,
            self._header_attr(1),
        )
        row += 1

        path_text = entry.path if entry and entry.path else ""
        stdscr.addnstr(
            row, col, path_text.ljust(width - 1)[: width - 1], width - 1, curses.A_DIM
        )
        row += 1

        count_text = f"{len(self.songs_panel.songs)} tracks"
        stdscr.addnstr(
            row, col, count_text.ljust(width - 1)[: width - 1], width - 1, curses.A_DIM
        )
        row += 1

        # Random button: fixed row, always visible above the scrollable list.
        btn_attr = curses.A_BOLD if self.active_column == 1 else curses.A_NORMAL
        stdscr.addnstr(
            row,
            col,
            "[R] Play Random".ljust(width - 1)[: width - 1],
            width - 1,
            btn_attr,
        )
        row += 1

        for track, idx, is_selected in self.songs_panel.get_visible_songs(height - row):
            attr = self._cursor_attr(1) if is_selected else curses.A_NORMAL
            line = f"{idx + 1}. {track.title}"[: width - 1]
            stdscr.addnstr(row, col, line.ljust(width - 1), width - 1, attr)
            row += 1

    def _render_queue(
        self, stdscr: curses._CursesWindow, row: int, col: int, width: int, height: int
    ) -> None:
        """Render now-playing track + upcoming queue."""
        head_attr = (
            (curses.color_pair(COLOR_QUEUE_HEAD) | curses.A_BOLD)
            if self._has_colors
            else curses.A_BOLD
        )

        stdscr.addnstr(
            row,
            col,
            "Now Playing".ljust(width - 1)[: width - 1],
            width - 1,
            self._header_attr(2),
        )
        row += 1

        current = self.queue_panel.current_track
        current_line = f"{current.artist} - {current.title}" if current else "(none)"
        current_line = current_line[: width - 1]
        stdscr.addnstr(row, col, current_line.ljust(width - 1), width - 1, head_attr)
        row += 1

        stdscr.addnstr(
            row,
            col,
            f"Queue ({len(self.queue_panel.queue)})".ljust(width - 1)[: width - 1],
            width - 1,
            curses.A_DIM,
        )
        row += 1

        for track, _ in self.queue_panel.get_visible_queue(height - row):
            line = f"{track.artist} - {track.title}"[: width - 1]
            stdscr.addnstr(row, col, line.ljust(width - 1), width - 1, curses.A_NORMAL)
            row += 1

    @staticmethod
    def _centered(text: str, width: int) -> str:
        """Pad `text` with leading spaces to center it within `width` columns."""
        text = text[:width]
        padding = max(0, (width - len(text)) // 2)
        return " " * padding + text

    def _render_player_bar(
        self, stdscr: curses._CursesWindow, row: int, width: int
    ) -> None:
        """Render bottom player bar: state/track/controls, then a progress bar."""
        state = self.player_bar.get_state_display()
        track_display = self.player_bar.get_track_display()
        controls = "[Space]Play/Pause [,]Back [N]ext [R]andom [Q]uit"

        bar_attr = (
            curses.color_pair(COLOR_PLAYER_BAR)
            if self._has_colors
            else curses.A_REVERSE
        )
        info_line = self._centered(f"[{state}] {track_display} | {controls}", width - 1)
        stdscr.addnstr(row, 0, info_line.ljust(width - 1), width - 1, bar_attr)

        progress_attr = (
            curses.color_pair(COLOR_PROGRESS) if self._has_colors else curses.A_NORMAL
        )
        progress_line = self._centered(
            self.player_bar.get_progress_display(), width - 1
        )
        stdscr.addnstr(
            row + 1, 0, progress_line.ljust(width - 1), width - 1, progress_attr
        )

        if self.player_bar.status_message:
            status_line = self._centered(self.player_bar.status_message, width - 1)
            stdscr.addnstr(
                row + 2, 0, status_line.ljust(width - 1), width - 1, curses.A_DIM
            )

    def _render_settings(
        self, stdscr: curses._CursesWindow, height: int, width: int
    ) -> None:
        """Full-screen settings editor, replacing the 3-column layout."""
        panel = self.settings_panel
        stdscr.addnstr(0, 0, "Settings".ljust(width - 1), width - 1, curses.A_BOLD)

        values = {
            "top_k": str(panel.top_k),
            "randomness": f"{panel.randomness:.2f}",
            "queue_length": str(panel.queue_length),
            "library_path": str(panel.library_path),
        }
        row = 2
        for index, field_name in enumerate(SETTINGS_FIELDS):
            attr = (
                self._cursor_attr(0) if index == panel.field_index else curses.A_NORMAL
            )
            line = f"{field_name}: {values[field_name]}"
            stdscr.addnstr(row, 2, line.ljust(width - 3)[: width - 3], width - 3, attr)
            row += 1

        row += 1
        if panel.status_message:
            stdscr.addnstr(
                row, 2, panel.status_message[: width - 3], width - 3, curses.A_DIM
            )
            row += 1

        hint = (
            "[Up/Down] Move  [+/-] Adjust  [Enter] Edit  [A] Apply&Save  [Esc] Cancel"
        )
        stdscr.addnstr(
            height - 1, 0, hint.ljust(width - 1)[: width - 1], width - 1, curses.A_DIM
        )


def run(
    library: Library,
    settings: Settings,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
) -> int:
    """Entry point for 3-column player."""
    try:
        backend = MpvBackend()
    except MpvUnavailableError as error:
        print(str(error))
        return 1

    players: list[Player3Column] = []
    try:

        def _main(stdscr: curses._CursesWindow) -> None:
            player = Player3Column(
                library, settings, backend, settings_path=settings_path
            )
            players.append(player)
            player.run_loop(stdscr)

        curses.wrapper(_main)
        return 0
    finally:
        if players and players[0]._mpris_service is not None:
            players[0]._mpris_service.shutdown()
        backend.shutdown()
