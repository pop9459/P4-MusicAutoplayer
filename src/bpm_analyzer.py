"""Tempo detection from the audio itself, for libraries with no BPM tags.

BPM carries a quarter of the similarity weight, but almost no real-world mp3
collection has the tag filled in -- 0 of 2599 tracks in the reference library.
Tempo is also the one signal that separates a 174 BPM track from a 90 BPM
ballad *within* a genre, which is exactly the distinction genre and year
cannot make. Detecting it locally restores that weight.

`aubio` is an optional dependency, imported lazily so neither the test suite
nor `python -m src.cli play` pays for it: without it, `track_similarity` just
keeps skipping the bpm term. Install it from the distro package
(`pacman -S python-aubio` on Arch) rather than pip, whose 0.4.9 predates
numpy 2.x and fails to build.

Detection is a separate, explicit command rather than part of `add-folder`:
it runs at roughly 0.2-0.5s per track, so folding it into a folder scan would
turn adding a folder into a half-hour block.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .track_analyzer import TrackRecord

# aubio reads in blocks; 512 frames at its default 44.1kHz hop is the size
# its own tempo example uses.
_SAMPLE_RATE = 0  # 0 means "use the file's own rate"
_WINDOW_SIZE = 1024
_HOP_SIZE = 512

# Detected tempi outside this range are treated as failures rather than
# stored: they are beat-tracker artifacts (silence, spoken word, free tempo),
# not tempi any of this library's music actually has.
_MIN_PLAUSIBLE_BPM = 40.0
_MAX_PLAUSIBLE_BPM = 250.0


class BpmAnalyzerUnavailableError(RuntimeError):
    """Raised when tempo detection is requested but aubio isn't installed."""


def _load_aubio():
    try:
        import aubio  # noqa: PLC0415 - deliberately lazy, see module docstring
    except ImportError as error:
        raise BpmAnalyzerUnavailableError(
            "Tempo detection needs the optional 'aubio' package.\n"
            "Install it from your distribution (Arch: sudo pacman -S python-aubio); "
            "avoid 'pip install aubio', which fails to build against numpy 2.x."
        ) from error
    return aubio


@contextlib.contextmanager
def _silenced_stderr() -> Iterator[None]:
    """Mute writes to file descriptor 2 for the duration of the block.

    aubio decodes mp3 through libav, which logs benign warnings ("Could not
    update timestamps for skipped samples" -- gapless-playback padding) for
    most files, straight to fd 2 from C. Python-level redirection doesn't
    reach it, and at two files a second the noise buries the progress line.

    Losing genuine stderr output here is acceptable because decoding failures
    surface as a None result, which the caller reports itself.
    """
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        sys.stderr.flush()
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def require_aubio() -> None:
    """Fail fast if aubio is missing, before any work is announced.

    Without this the caller prints "2599 tracks need a BPM, roughly 17 min"
    and only then dies on the first file.
    """
    _load_aubio()


def detect_bpm(path: str | Path) -> float | None:
    """Estimate one file's tempo, or None if it can't be determined.

    The raw estimate is stored as-is. Folding it into a fixed range would
    put an artificial seam in the middle of the scale -- 138 and 142 BPM
    would land at opposite ends -- so half/double-time equivalence is
    handled by the circular distance in `track_analyzer._bpm_similarity`
    instead, where it costs three lines and has no seam.
    """
    aubio = _load_aubio()
    try:
        with _silenced_stderr():
            source = aubio.source(str(path), _SAMPLE_RATE, _HOP_SIZE)
            tempo = aubio.tempo("default", _WINDOW_SIZE, _HOP_SIZE, source.samplerate)
            while True:
                samples, read = source()
                tempo(samples)
                if read < _HOP_SIZE:
                    break
            estimate = float(tempo.get_bpm())
    except Exception:
        # A corrupt or unreadable file must not abort a run over thousands
        # of tracks; the caller reports it and moves on.
        return None
    if not _MIN_PLAUSIBLE_BPM <= estimate <= _MAX_PLAUSIBLE_BPM:
        return None
    return round(estimate, 2)


def tracks_needing_bpm(tracks: Iterable[TrackRecord]) -> list[TrackRecord]:
    """Tracks with no BPM yet -- what makes a run resumable.

    An interrupted run leaves the tracks it did finish with a bpm, so simply
    skipping those on the next run picks up where it left off. No separate
    progress file, and no "failed" flag: re-trying the handful of unreadable
    files each run is cheaper than a schema field to remember them.
    """
    return [track for track in tracks if track.bpm is None]


def analyze_tracks(
    tracks: list[TrackRecord],
    *,
    checkpoint_every: int = 50,
    on_checkpoint: Callable[[], None] | None = None,
    on_progress: Callable[[int, int, TrackRecord], None] | None = None,
) -> tuple[int, list[TrackRecord]]:
    """Fill in `bpm` on each track in place, checkpointing as it goes.

    Returns (number analyzed, tracks whose tempo could not be determined).

    `on_checkpoint` is called every `checkpoint_every` tracks and once at the
    end, so the caller can persist partial results: a run over a few thousand
    files takes tens of minutes and will be interrupted, and losing twenty
    minutes of work to Ctrl-C would make the command not worth running.
    """
    total = len(tracks)
    analyzed = 0
    failed: list[TrackRecord] = []

    for index, track in enumerate(tracks, start=1):
        bpm = detect_bpm(track.path)
        if bpm is None:
            failed.append(track)
        else:
            track.bpm = bpm
            analyzed += 1
        if on_progress is not None:
            on_progress(index, total, track)
        if on_checkpoint is not None and index % checkpoint_every == 0:
            on_checkpoint()

    if on_checkpoint is not None and total:
        on_checkpoint()
    return analyzed, failed


@dataclass
class BpmTask:
    """Background tempo detection, polled from the TUI's render loop.

    Same shape as `library.ScanTask`, with the same rule: the thread never
    touches curses, mpv, or the catalog. It only reads each track's path and
    publishes finished results into a lock-guarded dict, which the UI thread
    drains and applies. The live catalog is therefore only ever written from
    one thread, even though playback and queue generation continue during a
    run that takes tens of minutes.
    """

    thread: threading.Thread | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    _progress: list[int] = field(default_factory=lambda: [0, 0])
    _results: dict[str, float] = field(default_factory=dict)
    _failed: list[int] = field(default_factory=lambda: [0])
    error: list[BaseException] = field(default_factory=list)

    def progress(self) -> tuple[int, int]:
        with self.lock:
            return self._progress[0], self._progress[1]

    def failed_count(self) -> int:
        with self.lock:
            return self._failed[0]

    def drain(self) -> dict[str, float]:
        """Take everything detected since the last call.

        Draining rather than accumulating means a long run applies its
        results as it goes, so an interrupted session still keeps the tempi
        it managed to detect.
        """
        with self.lock:
            drained = self._results
            self._results = {}
            return drained

    def cancel(self) -> None:
        self.cancelled.set()


def start_bpm_task(tracks: Iterable[TrackRecord]) -> BpmTask:
    """Analyze `tracks` on a background thread, reading them but never writing.

    Checks for aubio up front so a missing dependency surfaces immediately
    rather than as 2599 consecutive failures.
    """
    pending = list(tracks)
    task = BpmTask(thread=None)
    task._progress[1] = len(pending)

    def _run() -> None:
        try:
            require_aubio()
            for index, track in enumerate(pending, start=1):
                if task.cancelled.is_set():
                    break
                bpm = detect_bpm(track.path)
                with task.lock:
                    task._progress[0] = index
                    if bpm is None:
                        task._failed[0] += 1
                    else:
                        task._results[track.id] = bpm
        except BaseException as error:  # noqa: BLE001 - surfaced to the UI, never raised on this thread
            task.error.append(error)
        finally:
            task.done.set()

    thread = threading.Thread(target=_run, daemon=True)
    task.thread = thread
    thread.start()
    return task
