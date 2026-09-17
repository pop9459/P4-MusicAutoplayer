"""Drive a `PlayerEngine` from a seed track, shared by the measurement tools.

Every tool in `tools/` must measure the *same* playback path, or their numbers
stop being comparable with each other and with what the player actually does.
Sessions therefore run through `PlayerEngine.advance()` rather than
`generate_queue`, so the queue top-up, the artist cap and the work-key cooldown
are all exercised exactly as playback exercises them.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.player import PlayerEngine  # noqa: E402
from src.track_analyzer import Catalog, TrackRecord  # noqa: E402


def play_session(
    catalog: Catalog,
    seed: TrackRecord,
    length: int,
    rng_seed: int,
    settings,
    *,
    top_k: int | None = None,
    randomness: float | None = None,
    queue_length: int | None = None,
    max_consecutive_same_artist: int | None = -1,
) -> list[TrackRecord]:
    """Play `length` tracks on from `seed`, returning `[seed, *played]`.

    The keyword overrides let a caller vary one knob without building a whole
    `Settings`; each defaults to the settings value. `max_consecutive_same_artist`
    uses -1 rather than None as its "not overridden" marker, because None is a
    meaningful value for it (the cap disabled).
    """
    engine = PlayerEngine(
        catalog,
        seed,
        top_k=settings.top_k if top_k is None else top_k,
        randomness=settings.randomness if randomness is None else randomness,
        queue_length=settings.queue_length if queue_length is None else queue_length,
        rng=random.Random(rng_seed),
        max_consecutive_same_artist=(
            settings.max_consecutive_same_artist
            if max_consecutive_same_artist == -1
            else max_consecutive_same_artist
        ),
    )
    played = [seed]
    for _ in range(length):
        next_track = engine.advance()
        if next_track is None:
            break
        played.append(next_track)
    return played
