"""Minimal curses TUI player.

Wires the recommender core (:mod:`src.predictor`, :mod:`src.track_analyzer`)
and the mpv playback backend (:mod:`src.mpv_backend`) into a small full
screen terminal demo:

  1. pick a starting track from the enabled tracks in a catalog
  2. build a recommendation queue from that track
  3. play tracks back to back, advancing/refilling the queue as needed

The pieces that don't need a real terminal or a real mpv process
(:class:`PlayerEngine`, :func:`clamp_index`, :func:`format_track_line`) are
plain functions/classes so they can be unit tested directly.
"""
from __future__ import annotations

import curses
import random
from dataclasses import dataclass

from .mpv_backend import MpvBackend, MpvUnavailableError
from .predictor import generate_queue, recommend_next_track
from .track_analyzer import Catalog, TrackRecord

POLL_INTERVAL_MS = 200


def format_track_line(track: TrackRecord) -> str:
    return f"{track.artist} - {track.title}"


def filter_enabled_tracks(catalog: Catalog) -> list[TrackRecord]:
    return [track for track in catalog.tracks if track.enabled]


def clamp_index(index: int, length: int) -> int:
    """Clamp a list index into range, returning 0 for an empty list."""
    if length <= 0:
        return 0
    return max(0, min(index, length - 1))


class PlayerEngine:
    """Tracks current/queued playback state and refills the queue on demand.

    Pure Python, no curses/mpv dependency, so behavior (queue advancing,
    refilling on exhaustion) can be unit tested directly.
    """

    def __init__(
        self,
        catalog: Catalog,
        start_track: TrackRecord,
        top_k: int,
        randomness: float,
        queue_length: int,
        rng: random.Random | None = None,
        max_consecutive_same_artist: int | None = 3,
    ) -> None:
        self.catalog = catalog
        self.top_k = top_k
        self.randomness = randomness
        self.queue_length = queue_length
        self.rng = rng or random.Random()
        self.max_consecutive_same_artist = max_consecutive_same_artist
        self.current_track = start_track
        self.queue: list[TrackRecord] = []
        self.history: list[TrackRecord] = [start_track]
        self.queue_regenerated = False
        self._refill_if_needed()

    def _refill_if_needed(self) -> None:
        if self.queue:
            return
        self.queue = generate_queue(
            self.current_track.id,
            self.catalog,
            length=self.queue_length,
            top_k=self.top_k,
            randomness=self.randomness,
            rng=self.rng,
            max_consecutive_same_artist=self.max_consecutive_same_artist,
        )
        self.queue_regenerated = True

    def _top_up_queue(self) -> bool:
        """Append recommendations one at a time until the queue is back to
        `queue_length` (or no eligible tracks remain).

        Excludes the current track, the entire play history, and everything
        already queued, so no track repeats within the session while there
        are still unseen tracks to recommend. Returns True if at least one
        track was appended.
        """
        added_any = False
        recent_artists = [track.artist for track in self.history] + [track.artist for track in self.queue]
        while len(self.queue) < self.queue_length:
            reference_id = self.queue[-1].id if self.queue else self.current_track.id
            excluded_ids = {self.current_track.id}
            excluded_ids.update(track.id for track in self.history)
            excluded_ids.update(track.id for track in self.queue)
            try:
                next_track = recommend_next_track(
                    reference_id,
                    self.catalog,
                    top_k=self.top_k,
                    randomness=self.randomness,
                    rng=self.rng,
                    excluded_track_ids=excluded_ids,
                    recent_artists=recent_artists,
                    max_consecutive_same_artist=self.max_consecutive_same_artist,
                )
            except ValueError:
                break
            self.queue.append(next_track)
            recent_artists.append(next_track.artist)
            added_any = True
        return added_any

    def peek_next(self) -> TrackRecord | None:
        return self.queue[0] if self.queue else None

    def advance(self) -> TrackRecord | None:
        """Move to the next queued track, topping the queue back up to
        `queue_length` afterward.

        Returns the new current track, or None if the queue was already
        empty (fully exhausted library, no eligible next track anywhere).
        """
        self.queue_regenerated = False
        if not self.queue:
            return None
        self.current_track = self.queue.pop(0)
        self.history.append(self.current_track)
        self.queue_regenerated = self._top_up_queue()
        return self.current_track


@dataclass
class _UiState:
    view_mode: str = "now_playing"
    paused: bool = False
    status_message: str = ""


def _draw_now_playing(stdscr, engine: PlayerEngine, ui: _UiState) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    stdscr.addnstr(0, 0, "Music Autoplayer -- demo player", width - 1, curses.A_BOLD)

    state_label = "PAUSED" if ui.paused else "PLAYING"
    stdscr.addnstr(2, 0, f"[{state_label}] {format_track_line(engine.current_track)}", width - 1, curses.A_STANDOUT)
    stdscr.addnstr(3, 0, f"Track ID: {engine.current_track.id}", width - 1)

    next_track = engine.peek_next()
    next_label = format_track_line(next_track) if next_track else "(queue empty)"
    stdscr.addnstr(5, 0, f"Next up: {next_label}", width - 1)
    stdscr.addnstr(6, 0, f"Queue length: {len(engine.queue)}", width - 1)

    if ui.status_message:
        stdscr.addnstr(8, 0, ui.status_message, width - 1, curses.A_DIM)

    hint_row = height - 2
    stdscr.addnstr(
        hint_row,
        0,
        "p/space: play-pause   n: next   l: toggle queue view   q: quit",
        width - 1,
        curses.A_DIM,
    )
    stdscr.refresh()


def _draw_queue_view(stdscr, engine: PlayerEngine, ui: _UiState) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    stdscr.addnstr(0, 0, "Upcoming queue", width - 1, curses.A_BOLD)
    stdscr.addnstr(1, 0, f"Now playing: {format_track_line(engine.current_track)}", width - 1)

    row = 3
    for index, track in enumerate(engine.queue, start=1):
        if row >= height - 2:
            stdscr.addnstr(row, 0, f"... {len(engine.queue) - index + 1} more", width - 1, curses.A_DIM)
            break
        stdscr.addnstr(row, 0, f"{index}. {format_track_line(track)}", width - 1)
        row += 1

    stdscr.addnstr(
        height - 2,
        0,
        "p/space: play-pause   n: next   l: back to now playing   q: quit",
        width - 1,
        curses.A_DIM,
    )
    stdscr.refresh()


def pick_starting_track(stdscr, tracks: list[TrackRecord]) -> TrackRecord | None:
    """Scrollable picker over enabled tracks. Returns None if the user quits."""
    if not tracks:
        return None

    selected = 0
    top = 0
    curses.curs_set(0)
    stdscr.timeout(-1)

    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, "Select a starting track (arrows to move, Enter to select, q to quit)", width - 1, curses.A_BOLD)

        visible_rows = max(1, height - 2)
        if selected < top:
            top = selected
        elif selected >= top + visible_rows:
            top = selected - visible_rows + 1

        for row_offset, track in enumerate(tracks[top : top + visible_rows]):
            track_index = top + row_offset
            attr = curses.A_STANDOUT if track_index == selected else curses.A_NORMAL
            stdscr.addnstr(row_offset + 1, 0, format_track_line(track), width - 1, attr)

        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            selected = clamp_index(selected - 1, len(tracks))
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = clamp_index(selected + 1, len(tracks))
        elif key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            return tracks[selected]
        elif key in (ord("q"), ord("Q")):
            return None


def _run_playback_loop(stdscr, engine: PlayerEngine, backend: MpvBackend) -> None:
    ui = _UiState()
    stdscr.timeout(POLL_INTERVAL_MS)
    backend.load_file(engine.current_track.path)

    while True:
        if ui.view_mode == "now_playing":
            _draw_now_playing(stdscr, engine, ui)
        else:
            _draw_queue_view(stdscr, engine, ui)

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key in (ord("p"), ord("P"), ord(" ")):
            ui.paused = backend.toggle_pause()
            ui.status_message = ""
        elif key in (ord("n"), ord("N")):
            if not _advance_and_load(engine, backend, ui):
                return
        elif key in (ord("l"), ord("L")):
            ui.view_mode = "queue" if ui.view_mode == "now_playing" else "now_playing"
        elif key == -1 and not ui.paused and backend.is_finished():
            if not _advance_and_load(engine, backend, ui):
                return


def _advance_and_load(engine: PlayerEngine, backend: MpvBackend, ui: _UiState) -> bool:
    """Advance the engine and load the next track into the backend.

    Returns False when there is no next track (nothing left to play).
    """
    next_track = engine.advance()
    if next_track is None:
        ui.status_message = "Queue exhausted -- no eligible tracks remain."
        return False
    backend.load_file(next_track.path)
    ui.paused = False
    ui.status_message = "Queue regenerated." if engine.queue_regenerated else ""
    return True


def run(
    catalog: Catalog,
    top_k: int,
    randomness: float,
    queue_length: int,
    start_track_id: str | None = None,
) -> int:
    """Entry point used by ``src.cli play``. Returns a process exit code."""
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
        def _main(stdscr) -> TrackRecord | None:
            nonlocal start_track
            if start_track is None:
                start_track = pick_starting_track(stdscr, enabled_tracks)
            if start_track is None:
                return None
            engine = PlayerEngine(
                catalog,
                start_track,
                top_k=top_k,
                randomness=randomness,
                queue_length=queue_length,
            )
            _run_playback_loop(stdscr, engine, backend)
            return start_track

        result = curses.wrapper(_main)
        if result is None:
            print("No track selected. Exiting.")
            return 0
        return 0
    finally:
        backend.shutdown()
