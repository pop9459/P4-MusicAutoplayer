"""Stateful playback engine, independent of curses and mpv.

`PlayerEngine` owns the current track, the recommendation queue, and the
play history. It calls into the recommender core (:mod:`src.predictor`) to
refill the queue, but knows nothing about rendering or audio output, so its
behaviour -- advancing, topping up, going back -- is unit tested directly.

`QueueTask`/`start_queue_task` run a queue build on a background thread. The
thread only appends to `PlayerEngine.queue` under that engine's own lock and
to the task's own state; it never touches curses or mpv.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .predictor import generate_queue_steps, recommend_next_track
from .track_analyzer import Catalog, TrackRecord

def filter_enabled_tracks(catalog: Catalog) -> list[TrackRecord]:
    return [track for track in catalog.tracks if track.enabled]


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
        defer_queue: bool = False,
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
        self._queue_lock = threading.Lock()
        if not defer_queue:
            self._refill_if_needed()

    def ensure_queue_ready(self) -> None:
        """Build the initial queue if it hasn't been built yet.

        Lets a caller start mpv playback (`defer_queue=True` at construction)
        before paying the cost of `generate_queue`'s full-catalog scan, so
        audio starts immediately instead of waiting behind queue assembly.
        """
        self._refill_if_needed()

    def build_initial_queue_steps(self) -> Iterator[TrackRecord]:
        """Build the initial queue one track at a time, appending each pick
        to `self.queue` and yielding it as it's chosen -- lets a caller (the
        TUI) reveal the queue filling in rather than updating it all at once.
        """
        for next_track in generate_queue_steps(
            self.current_track.id,
            self.catalog,
            length=self.queue_length,
            top_k=self.top_k,
            randomness=self.randomness,
            rng=self.rng,
            max_consecutive_same_artist=self.max_consecutive_same_artist,
        ):
            with self._queue_lock:
                self.queue.append(next_track)
            yield next_track
        self.queue_regenerated = True

    def queue_snapshot(self) -> list[TrackRecord]:
        """Thread-safe copy of `self.queue`, for a caller (e.g. the TUI's
        render loop) reading the queue while a background thread may still
        be appending to it via `build_initial_queue_steps`/`top_up_queue_steps`."""
        with self._queue_lock:
            return list(self.queue)

    def _refill_if_needed(self) -> None:
        if self.queue:
            return
        list(self.build_initial_queue_steps())

    def top_up_queue_steps(self) -> Iterator[TrackRecord]:
        """Append recommendations one at a time until the queue is back to
        `queue_length` (or no eligible tracks remain), yielding each track as
        it's appended.

        Excludes the current track, the entire play history, and everything
        already queued, so no track repeats within the session while there
        are still unseen tracks to recommend.

        Separately suppresses other *versions* of a recently played song --
        a duplicate from an overlapping folder, or a remix -- over a sliding
        window rather than the whole session, since a third of the library
        belongs to a multi-version group and banning them outright would put
        much of it out of reach.
        """
        recent_artists = [track.artist for track in self.history] + [track.artist for track in self.queue]
        recent_work_keys = [
            self.catalog.features_for(track).work_key
            for track in self.history[-self.queue_length :] + [self.current_track] + self.queue
        ]
        # Built once and extended as picks land: rebuilding it from the full
        # history and queue on every slot made a long top-up quadratic.
        excluded_ids = {self.current_track.id}
        excluded_ids.update(track.id for track in self.history)
        excluded_ids.update(track.id for track in self.queue)
        while len(self.queue) < self.queue_length:
            reference_id = self.queue[-1].id if self.queue else self.current_track.id
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
                    recent_work_keys=recent_work_keys,
                )
            except ValueError:
                break
            with self._queue_lock:
                self.queue.append(next_track)
            excluded_ids.add(next_track.id)
            recent_artists.append(next_track.artist)
            recent_work_keys.append(self.catalog.features_for(next_track).work_key)
            yield next_track

    def _top_up_queue(self) -> bool:
        added_any = False
        for _ in self.top_up_queue_steps():
            added_any = True
        return added_any

    def peek_next(self) -> TrackRecord | None:
        return self.queue[0] if self.queue else None

    def advance_immediate(self) -> TrackRecord | None:
        """Move to the next queued track without refilling the queue.

        Lets a caller start playback on the new current track before paying
        the cost of `_top_up_queue`'s recommendation scan. Returns the new
        current track, or None if the queue was already empty.
        """
        self.queue_regenerated = False
        if not self.queue:
            return None
        self.current_track = self.queue.pop(0)
        self.history.append(self.current_track)
        return self.current_track

    def top_up_queue(self) -> bool:
        """Refill the queue back up to `queue_length`. See `_top_up_queue`."""
        self.queue_regenerated = self._top_up_queue()
        return self.queue_regenerated

    def go_back(self) -> TrackRecord | None:
        """Move to the previous track in history, pushing the current track
        back onto the front of the queue.

        `history`'s invariant is "last element is always the current
        track" (see `advance_immediate`); this pops the current entry,
        pops the actual previous track, and re-appends it to restore that
        invariant. Returns the restored track, or None if there is no
        previous track to go back to (history has one or zero entries).
        """
        if len(self.history) < 2:
            return None
        self.history.pop()
        previous_track = self.history.pop()
        self.queue.insert(0, self.current_track)
        self.current_track = previous_track
        self.history.append(self.current_track)
        return self.current_track

    def advance(self) -> TrackRecord | None:
        """Move to the next queued track, topping the queue back up to
        `queue_length` afterward.

        Returns the new current track, or None if the queue was already
        empty (fully exhausted library, no eligible next track anywhere).
        """
        next_track = self.advance_immediate()
        if next_track is None:
            return None
        self.top_up_queue()
        return next_track


@dataclass
class QueueTask:
    """Wraps a background thread that drains a `PlayerEngine` queue-building
    generator (`build_initial_queue_steps`/`top_up_queue_steps`), polled from
    the TUI's render loop instead of consumed inline -- so the full-catalog
    similarity scan behind each queue slot never blocks curses input. The
    thread only mutates `PlayerEngine.queue` (under its own lock) and this
    task's own state; it never touches curses/mpv."""

    thread: threading.Thread | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    done: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    _revealed: list[int] = field(default_factory=lambda: [0])
    error: list[BaseException] = field(default_factory=list)

    def revealed_count(self) -> int:
        with self.lock:
            return self._revealed[0]


def start_queue_task(steps_factory: Callable[[], Iterator[TrackRecord]]) -> QueueTask:
    task = QueueTask(thread=None)

    def _run() -> None:
        try:
            for _ in steps_factory():
                if task.cancel.is_set():
                    break
                with task.lock:
                    task._revealed[0] += 1
        except Exception as error:  # generation shouldn't normally raise; surface if it does
            task.error.append(error)
        finally:
            task.done.set()

    thread = threading.Thread(target=_run, daemon=True)
    task.thread = thread
    thread.start()
    return task
