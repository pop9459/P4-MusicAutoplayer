"""Measure how the recommender actually behaves on a real library.

Recommender changes are easy to argue about and hard to judge by ear, so this
reports the numbers a change should be defended with. Every figure here was
what settled whether to build GitHub #10 (it said no: sessions were already
staying put, and anchoring the top-up would have cost a fifth of their
variety for a 0.002 change in similarity to the seed).

Sessions are driven through `PlayerEngine.advance()` rather than
`generate_queue`, so the measurements exercise the queue top-up, the artist
cap and the work-key cooldown exactly as playback does.

Deterministic by default, so two runs are comparable:

    python tools/recommender_report.py
    python tools/recommender_report.py --sessions 5 --length 30
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.library import load_library  # noqa: E402
from src.player import PlayerEngine  # noqa: E402
from src.predictor import rank_candidates  # noqa: E402
from src.settings import DEFAULT_SETTINGS_PATH, load_settings  # noqa: E402
from src.track_analyzer import Catalog, TrackRecord, genre_grouping, track_similarity  # noqa: E402

# A top-1 score at or above this is a tie for practical purposes: the tracks
# agree on every field the scoring can see, so which one plays next is
# effectively arbitrary.
TIE_THRESHOLD = 0.999


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _similarity(catalog: Catalog, left: TrackRecord, right: TrackRecord) -> float:
    return track_similarity(catalog.features_for(left), catalog.features_for(right))


def report_coverage(catalog: Catalog) -> None:
    """How much of the library each signal can actually speak for.

    A signal missing on one side of a pair is skipped and its weight
    redistributed, so poor coverage quietly shifts what is driving the
    recommendations.
    """
    total = len(catalog.tracks)
    with_tempo = sum(1 for track in catalog.tracks if track.bpm is not None)
    with_year = sum(1 for track in catalog.tracks if track.year is not None)
    ungrouped = sum(1 for track in catalog.tracks if genre_grouping(track.genre) == (None, None))
    unknown_genre = sum(1 for track in catalog.tracks if track.genre == "unknown")

    print("Coverage")
    print(f"  tracks                      {total}")
    print(f"  with a tempo                {with_tempo} ({with_tempo * 100 // max(total, 1)}%)")
    print(f"  with a year                 {with_year} ({with_year * 100 // max(total, 1)}%)")
    print(f"  genre matches nothing       {ungrouped} ({unknown_genre} of them untagged)")


def report_saturation(catalog: Catalog, tracks: list[TrackRecord], rng: random.Random, sample_size: int) -> None:
    """How often the top candidate is a tie.

    High saturation means ranking has run out of things to distinguish tracks
    by and the pick is effectively random. This ran at 58% before any of the
    scoring work, 42% after it, and 28% once tempo data existed.
    """
    sample = rng.sample(tracks, min(sample_size, len(tracks)))
    top_scores = [rank_candidates(track.id, catalog)[0][1] for track in sample]
    ties = sum(1 for score in top_scores if score >= TIE_THRESHOLD)

    print("Saturation")
    print(f"  sampled                     {len(sample)} tracks")
    print(f"  top-1 ties (>= {TIE_THRESHOLD})     {ties * 100 // len(sample)}%")
    print(f"  mean top-1 similarity       {_mean(top_scores):.3f}")


def _play_session(catalog: Catalog, seed: TrackRecord, length: int, rng_seed: int, settings) -> list[TrackRecord]:
    engine = PlayerEngine(
        catalog,
        seed,
        top_k=settings.top_k,
        randomness=settings.randomness,
        queue_length=settings.queue_length,
        rng=random.Random(rng_seed),
        max_consecutive_same_artist=settings.max_consecutive_same_artist,
    )
    played = [seed]
    for _ in range(length):
        next_track = engine.advance()
        if next_track is None:
            break
        played.append(next_track)
    return played


def report_sessions(
    catalog: Catalog,
    tracks: list[TrackRecord],
    rng: random.Random,
    settings,
    sessions: int,
    length: int,
) -> None:
    """What a long listening session looks like, end to end.

    "to seed, last 10" vs "first 10" is the drift question; "consecutive" is
    whether it still hangs together; the distinct counts are breadth. Breadth
    is the current known weakness -- around two genre labels per session --
    and no `top_k`/`randomness` setting changes it.
    """
    seeds = rng.sample(tracks, min(sessions, len(tracks)))
    rows = []
    repeated_work = 0
    artist_shares = []

    for index, seed in enumerate(seeds):
        played = _play_session(catalog, seed, length, rng_seed=index, settings=settings)
        if len(played) < max(10, length // 2):
            continue
        to_seed = [_similarity(catalog, played[0], track) for track in played[1:]]
        consecutive = [_similarity(catalog, a, b) for a, b in zip(played, played[1:])]
        tempi = [track.bpm for track in played if track.bpm]
        work_keys = [catalog.features_for(track).work_key for track in played]
        if len(work_keys) != len(set(work_keys)):
            repeated_work += 1
        artist_shares.append(Counter(track.artist for track in played).most_common(1)[0][1])
        rows.append(
            {
                "first": _mean(to_seed[:10]),
                "last": _mean(to_seed[-10:]),
                "consecutive": _mean(consecutive),
                "genres": len({track.genre for track in played}),
                "families": len({catalog.features_for(track).family for track in played}),
                "artists": len({track.artist for track in played}),
                "tempo_span": max(tempi) - min(tempi) if tempi else 0.0,
                "length": len(played),
            }
        )

    if not rows:
        print("Sessions\n  library too small to simulate a session")
        return

    print(f"Sessions ({len(rows)} x {length} picks, top_k={settings.top_k}, randomness={settings.randomness})")
    print(f"  similarity to seed, first 10   {_mean([r['first'] for r in rows]):.3f}")
    print(f"  similarity to seed, last 10    {_mean([r['last'] for r in rows]):.3f}")
    print(f"  similarity between neighbours  {_mean([r['consecutive'] for r in rows]):.3f}")
    print(f"  distinct genre labels          {_mean([r['genres'] for r in rows]):.1f}")
    print(f"  distinct genre families        {_mean([r['families'] for r in rows]):.1f}")
    print(f"  distinct artists               {_mean([r['artists'] for r in rows]):.1f}")
    print(f"  tempo range spanned            {_mean([r['tempo_span'] for r in rows]):.0f} BPM")
    print()
    print("Repeats")
    print(f"  sessions replaying a song      {repeated_work}/{len(rows)}")
    print(f"  largest one-artist share       worst {max(artist_shares)}, mean {_mean(artist_shares):.1f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH, help="Settings JSON path.")
    parser.add_argument("--sessions", type=int, default=12, help="How many listening sessions to simulate.")
    parser.add_argument("--length", type=int, default=60, help="Tracks played per simulated session.")
    parser.add_argument("--sample", type=int, default=200, help="Tracks sampled for the saturation check.")
    parser.add_argument("--seed", type=int, default=0, help="Random seed; fixed so runs are comparable.")
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    library_path = args.library or settings.library_path
    library = load_library(library_path)
    catalog = library.catalog
    tracks = [track for track in catalog.tracks if track.enabled]

    if not tracks:
        print(f"No enabled tracks in {library_path}.")
        return 1

    print(f"Library: {library_path}")
    print()
    report_coverage(catalog)
    print()
    report_saturation(catalog, tracks, random.Random(args.seed), args.sample)
    print()
    report_sessions(catalog, tracks, random.Random(args.seed + 1), settings, args.sessions, args.length)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
