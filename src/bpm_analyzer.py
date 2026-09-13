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

from pathlib import Path
from typing import Callable, Iterable

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
