"""Curses rendering functions for each TUI tab."""
from __future__ import annotations

import curses
from dataclasses import dataclass

from .player import PlayerEngine
from .track_analyzer import TrackRecord


@dataclass
class RenderedLine:
    """Represents a line to render with optional attributes."""
    text: str
    attribute: int = curses.A_NORMAL


def format_track_line(track: TrackRecord) -> str:
    """Format a track record as a display line."""
    return f"{track.artist} - {track.title}"


def clamp_index(index: int, length: int) -> int:
    """Clamp a list index into range, returning 0 for an empty list."""
    if length <= 0:
        return 0
    return max(0, min(index, length - 1))


def draw_player_tab(
    stdscr: curses._CursesWindow,
    engine: PlayerEngine,
    paused: bool,
    status_message: str,
) -> None:
    """Render the player tab showing current track and playback state."""
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    # Header
    stdscr.addnstr(0, 0, "Music Autoplayer -- Player", width - 1, curses.A_BOLD)

    # Playback state
    state_label = "PAUSED" if paused else "PLAYING"
    line = f"[{state_label}] {format_track_line(engine.current_track)}"
    stdscr.addnstr(2, 0, line, width - 1, curses.A_STANDOUT)

    # Track ID
    stdscr.addnstr(3, 0, f"Track ID: {engine.current_track.id}", width - 1)

    # Next track
    next_track = engine.peek_next()
    next_label = format_track_line(next_track) if next_track else "(queue empty)"
    stdscr.addnstr(5, 0, f"Next up: {next_label}", width - 1)

    # Queue info
    stdscr.addnstr(6, 0, f"Queue length: {len(engine.queue)}", width - 1)

    # Status message
    if status_message:
        stdscr.addnstr(8, 0, status_message, width - 1, curses.A_DIM)

    # Help
    hint_row = height - 2
    hints = "p/space: play/pause | n: next | l: queue | s: settings | q: quit"
    stdscr.addnstr(hint_row, 0, hints, width - 1, curses.A_DIM)

    stdscr.refresh()


def draw_queue_tab(stdscr: curses._CursesWindow, engine: PlayerEngine) -> None:
    """Render the queue tab showing upcoming tracks."""
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    # Header
    stdscr.addnstr(0, 0, "Upcoming Queue", width - 1, curses.A_BOLD)
    stdscr.addnstr(1, 0, f"Now playing: {format_track_line(engine.current_track)}", width - 1)

    # Queue items
    row = 3
    for index, track in enumerate(engine.queue, start=1):
        if row >= height - 2:
            remaining = len(engine.queue) - index + 1
            stdscr.addnstr(row, 0, f"... {remaining} more", width - 1, curses.A_DIM)
            break
        stdscr.addnstr(row, 0, f"{index}. {format_track_line(track)}", width - 1)
        row += 1

    # Help
    stdscr.addnstr(height - 2, 0, "Tab: switch views | q: quit", width - 1, curses.A_DIM)

    stdscr.refresh()


def draw_folders_tab(
    stdscr: curses._CursesWindow,
    music_directory: str,
    edit_mode: bool,
    edit_field: str = "",
    status_message: str = "",
) -> None:
    """Render the folders tab for managing music directories."""
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    # Header
    stdscr.addnstr(0, 0, "Folder Settings", width - 1, curses.A_BOLD)

    # Current folder
    row = 2
    stdscr.addnstr(row, 0, "Music Directory:", width - 1)
    row += 1

    if edit_mode:
        # Edit mode: show input field
        stdscr.addnstr(row, 0, f"> {edit_field}", width - 1, curses.A_STANDOUT)
        row += 2
        stdscr.addnstr(
            row,
            0,
            "Enter: confirm | Esc: cancel",
            width - 1,
            curses.A_DIM,
        )
    else:
        # Read-only mode: show path and instructions
        stdscr.addnstr(row, 0, f"  {music_directory}", width - 1)
        row += 2
        stdscr.addnstr(row, 0, "Press 'e' to edit folder path", width - 1, curses.A_DIM)

    # Status message
    if status_message:
        row = height - 4
        stdscr.addnstr(row, 0, status_message, width - 1, curses.A_DIM)

    # Help
    stdscr.addnstr(height - 2, 0, "Tab: switch views | q: quit", width - 1, curses.A_DIM)

    stdscr.refresh()


def draw_settings_tab(
    stdscr: curses._CursesWindow,
    top_k: int,
    randomness: float,
    queue_length: int,
    edit_mode: bool,
    edit_field_index: int = 0,
    edit_values: list[str] | None = None,
    status_message: str = "",
) -> None:
    """Render the settings tab for editing recommendation parameters."""
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    # Header
    stdscr.addnstr(0, 0, "Recommendation Settings", width - 1, curses.A_BOLD)

    if edit_values is None:
        edit_values = [str(top_k), f"{randomness:.2f}", str(queue_length)]

    # Settings labels and values
    row = 2
    field_names = ["top-k (candidate pool size)", "randomness (0.0 to 1.0)", "queue length"]
    field_values = [top_k, randomness, queue_length]
    field_formats = [str, lambda x: f"{x:.2f}", str]

    for index, (name, value, formatter) in enumerate(zip(field_names, field_values, field_formats)):
        attr = curses.A_STANDOUT if (edit_mode and index == edit_field_index) else curses.A_NORMAL
        stdscr.addnstr(row, 0, f"{name}:", width - 1, attr)
        row += 1

        if edit_mode and index == edit_field_index:
            # Show edit field
            stdscr.addnstr(row, 0, f"  {edit_values[index]}", width - 1, curses.A_STANDOUT)
        else:
            # Show current value
            stdscr.addnstr(row, 0, f"  {formatter(value)}", width - 1)
        row += 2

    if edit_mode:
        stdscr.addnstr(
            row,
            0,
            "Up/Down: move field | Enter: save | Esc: cancel",
            width - 1,
            curses.A_DIM,
        )
    else:
        stdscr.addnstr(row, 0, "Press 'e' to edit settings", width - 1, curses.A_DIM)

    # Status message
    if status_message:
        status_row = height - 4
        stdscr.addnstr(status_row, 0, status_message, width - 1, curses.A_DIM)

    # Help
    stdscr.addnstr(height - 2, 0, "Tab: switch views | q: quit", width - 1, curses.A_DIM)

    stdscr.refresh()


def draw_tab_header(stdscr: curses._CursesWindow, active_tab: str) -> None:
    """Draw the tab bar showing all tabs and highlighting the active one."""
    tabs = ["[P]layer", "[Q]ueue", "[F]olders", "[S]ettings"]
    tab_names = ["player", "queue", "folders", "settings"]
    tab_line = "  " + "    ".join(
        f"{tab}" if tab_name != active_tab else f"→ {tab} ←"
        for tab, tab_name in zip(tabs, tab_names)
    )
    stdscr.addnstr(1, 0, tab_line, stdscr.getmaxyx()[1] - 1, curses.A_BOLD)
