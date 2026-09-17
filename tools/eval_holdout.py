"""Judge the recommender against a ground truth it has never seen: the album tag.

`tools/recommender_report.py` measures the *shape* of what the recommender does
-- saturation, drift, breadth, repeats. Those numbers can show that the
recommender has gone degenerate; none of them can show that it is good, because
none has a baseline or a right answer to be compared against.

This tool supplies one. `TrackRecord.album` is read and stored but deliberately
absent from `track_similarity`, so album membership is a human-curated
"these belong together" grouping the scorer has never been shown. Asking the
metric to retrieve a track's album-mates out of the whole library is therefore
a real question with a real answer, and a random scorer gives the floor.

    python tools/eval_holdout.py
    python tools/eval_holdout.py --same-artist-only
    python tools/eval_holdout.py --limit 200 --no-ablation

WHAT THIS PROXY CANNOT TELL YOU. Albums are strongly artist- and year-coherent,
so this criterion treats genre as largely redundant with artist and cannot
credit a term whose whole job is bridging *between* artists. A high score here
means the metric has found real structure; it does not mean the queues are
enjoyable. That question needs `tools/ab_listen.py`. Read the term-coherence
table below the ablation before concluding a weight is useless -- a term can
look unhelpful in the ablation while clearly tracking album membership on its
own, which means over-weighted, not uninformative.
"""
from __future__ import annotations

import argparse
import contextlib
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.library import load_library  # noqa: E402
from src.settings import DEFAULT_SETTINGS_PATH, load_settings  # noqa: E402
from src.track_analyzer import (  # noqa: E402
    FEATURE_WEIGHTS,
    Catalog,
    TrackFeatures,
    TrackRecord,
    _artist_similarity,
    _bpm_similarity,
    _genre_similarity,
    _year_similarity,
    track_similarity,
)

Scorer = Callable[[TrackFeatures, TrackFeatures], float]

# A same-artist pool smaller than this can't say anything: with three
# candidates, even a coin flip looks like skill.
_MIN_POOL = 5

# Random pairs sampled for the term-coherence baseline. Large enough that the
# mean is stable to ~0.005, small enough to stay instant.
_COHERENCE_SAMPLE = 20000


@contextlib.contextmanager
def feature_weights(overrides: Mapping[str, float]) -> Iterator[None]:
    """Temporarily patch FEATURE_WEIGHTS, restoring it on the way out.

    `track_similarity` reads the table per call and `TrackFeatures` never
    embeds a weight, so swapping it needs no cache invalidation. Restoring in
    a `finally` matters: an ablation row that raises must not leave every
    later row measuring the wrong weights.
    """
    original = dict(FEATURE_WEIGHTS)
    FEATURE_WEIGHTS.update(overrides)
    try:
        yield
    finally:
        FEATURE_WEIGHTS.clear()
        FEATURE_WEIGHTS.update(original)


def build_album_pairs(catalog: Catalog) -> dict[str, set[str]]:
    """Map each track id to the ids of its album-mates.

    Singleton albums are dropped -- a track with no mate is not a question
    anyone can get right or wrong. An empty album tag is not an album.
    """
    by_album: dict[str, list[TrackRecord]] = defaultdict(list)
    for track in catalog.tracks:
        if track.enabled and track.album:
            by_album[track.album.casefold()].append(track)

    mates: dict[str, set[str]] = {}
    for group in by_album.values():
        if len(group) < 2:
            continue
        ids = {track.id for track in group}
        for track in group:
            mates[track.id] = ids - {track.id}
    return mates


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    label: str
    queries: int
    precision_at_1: float
    recall_at_10: float
    mrr: float


def evaluate_retrieval(
    catalog: Catalog,
    album_mates: Mapping[str, set[str]],
    score: Scorer,
    label: str,
    *,
    same_artist_only: bool = False,
    queries: Sequence[TrackRecord] | None = None,
) -> RetrievalResult:
    """Rank every other track against each query and find its first album-mate.

    `same_artist_only` restricts the candidate pool to the query's own artist.
    That is the honest variant: album-mates almost always share an artist and a
    year, so on the full library the artist and year terms can carry the score
    on their own. Holding the artist fixed forces genre and tempo to do the
    discriminating, and the random baseline rises accordingly -- which is the
    point, a baseline that moves with the task is the only kind worth having.
    """
    tracks = [track for track in catalog.tracks if track.enabled]
    pool_by_artist: dict[str, list[TrackRecord]] = defaultdict(list)
    if same_artist_only:
        for track in tracks:
            pool_by_artist[track.artist].append(track)

    if queries is None:
        queries = [track for track in tracks if album_mates.get(track.id)]

    hits_at_1 = 0
    hits_at_10 = 0
    reciprocal_ranks: list[float] = []

    for query in queries:
        mates = album_mates.get(query.id)
        if not mates:
            continue
        candidates = pool_by_artist[query.artist] if same_artist_only else tracks
        pool = [track for track in candidates if track.id != query.id]
        if same_artist_only and (len(pool) < _MIN_POOL or not mates & {t.id for t in pool}):
            continue

        reference = catalog.features_for(query)
        ranked = sorted(pool, key=lambda t: score(reference, catalog.features_for(t)), reverse=True)
        position = next((i for i, track in enumerate(ranked) if track.id in mates), None)

        if position is None:
            reciprocal_ranks.append(0.0)
            continue
        reciprocal_ranks.append(1.0 / (position + 1))
        if position == 0:
            hits_at_1 += 1
        if position < 10:
            hits_at_10 += 1

    count = len(reciprocal_ranks)
    if count == 0:
        return RetrievalResult(label, 0, 0.0, 0.0, 0.0)
    return RetrievalResult(
        label=label,
        queries=count,
        precision_at_1=hits_at_1 / count,
        recall_at_10=hits_at_10 / count,
        mrr=statistics.mean(reciprocal_ranks),
    )


def format_result(result: RetrievalResult) -> str:
    return (
        f"  {result.label:<28} n={result.queries:<5} "
        f"P@1={result.precision_at_1:6.3f}  R@10={result.recall_at_10:6.3f}  MRR={result.mrr:6.3f}"
    )


def ablation_weights() -> list[tuple[str, dict[str, float]]]:
    """The weight tables to compare, current first."""
    current = dict(FEATURE_WEIGHTS)
    rows: list[tuple[str, dict[str, float]]] = [("current", current)]
    for name in current:
        rows.append((f"without {name}", {**current, name: 0.0}))
    for name in current:
        rows.append((f"{name} only", {key: (1.0 if key == name else 0.0) for key in current}))
    rows.append(("equal weights", {key: 0.25 for key in current}))
    return rows


def report_term_coherence(catalog: Catalog, album_mates: Mapping[str, set[str]], rng: random.Random) -> None:
    """Per term: mean similarity within an album vs. between random tracks.

    This is what separates "this term is noise" from "this term is real signal
    that is over-weighted". The ablation table alone cannot tell those apart:
    both look like "removing it helps".
    """
    terms: dict[str, Callable[[TrackFeatures, TrackFeatures], float | None]] = {
        "genre": _genre_similarity,
        "bpm": _bpm_similarity,
        "artist": _artist_similarity,
        "year": _year_similarity,
    }
    tracks = [track for track in catalog.tracks if track.enabled]
    features = {track.id: catalog.features_for(track) for track in tracks}
    by_id = {track.id: track for track in tracks}

    within: dict[str, list[float]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for track_id, mates in album_mates.items():
        for mate_id in mates:
            pair = (track_id, mate_id) if track_id < mate_id else (mate_id, track_id)
            if pair in seen or mate_id not in by_id or track_id not in by_id:
                continue
            seen.add(pair)
            for name, term in terms.items():
                value = term(features[track_id], features[mate_id])
                if value is not None:
                    within[name].append(value)

    between: dict[str, list[float]] = defaultdict(list)
    for _ in range(_COHERENCE_SAMPLE):
        left, right = rng.sample(tracks, 2)
        for name, term in terms.items():
            value = term(features[left.id], features[right.id])
            if value is not None:
                between[name].append(value)

    print("Term coherence (does this term track album membership at all?)")
    print(f"  {'term':<10} {'within album':>13} {'random pair':>13} {'lift':>8}")
    for name in terms:
        inside = statistics.mean(within[name]) if within[name] else 0.0
        outside = statistics.mean(between[name]) if between[name] else 0.0
        print(f"  {name:<10} {inside:13.3f} {outside:13.3f} {inside - outside:+8.3f}")
    print()
    print("  A term with a clear lift but an unhelpful ablation row is over-weighted,")
    print("  not uninformative -- it is crowding out a stronger term, not adding noise.")


def report_retrieval(
    catalog: Catalog,
    album_mates: Mapping[str, set[str]],
    rng: random.Random,
    *,
    same_artist_only: bool,
    ablation: bool,
    limit: int | None,
) -> None:
    tracks = [track for track in catalog.tracks if track.enabled]
    queries = [track for track in tracks if album_mates.get(track.id)]
    if limit is not None and limit < len(queries):
        queries = rng.sample(queries, limit)

    scope = "same-artist pool" if same_artist_only else "full library pool"
    print(f"Album-mate retrieval ({scope})")

    def run(label: str, score: Scorer) -> RetrievalResult:
        return evaluate_retrieval(
            catalog, album_mates, score, label, same_artist_only=same_artist_only, queries=queries
        )

    baseline_rng = random.Random(rng.randrange(2**32))
    print(format_result(run("current similarity", track_similarity)))
    print(format_result(run("random baseline", lambda a, b: baseline_rng.random())))

    if not ablation:
        return

    print()
    print("Leave-one-out / single-term ablation")
    for label, weights in ablation_weights():
        with feature_weights(weights):
            print(format_result(run(label, track_similarity)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH, help="Settings JSON path.")
    parser.add_argument(
        "--same-artist-only",
        action="store_true",
        help="Restrict candidates to the query's own artist, so genre/tempo must do the work.",
    )
    parser.add_argument("--no-ablation", action="store_true", help="Skip the weight ablation table.")
    parser.add_argument("--limit", type=int, help="Sample this many query tracks instead of using all of them.")
    parser.add_argument("--seed", type=int, default=0, help="Random seed; fixed so runs are comparable.")
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    library_path = args.library or settings.library_path
    library = load_library(library_path)
    catalog = library.catalog

    if not any(track.enabled for track in catalog.tracks):
        print(f"No enabled tracks in {library_path}.")
        return 1

    album_mates = build_album_pairs(catalog)
    if not album_mates:
        print(f"No album-mates in {library_path}: every album has a single track, so there is")
        print("no held-out grouping to score against. Nothing to measure.")
        return 1

    albums = len({frozenset(mates | {track_id}) for track_id, mates in album_mates.items()})
    print(f"Library: {library_path}")
    print(f"Ground truth: {len(album_mates)} tracks across {albums} albums of 2+ tracks")
    print()
    report_retrieval(
        catalog,
        album_mates,
        random.Random(args.seed),
        same_artist_only=args.same_artist_only,
        ablation=not args.no_ablation,
        limit=args.limit,
    )
    print()
    report_term_coherence(catalog, album_mates, random.Random(args.seed + 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
