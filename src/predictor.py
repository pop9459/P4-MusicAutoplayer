from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Collection, Sequence

from .track_analyzer import Catalog, TrackRecord, load_catalog


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Feature vectors must have the same length")

    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return _cosine_similarity_with_norms(left, left_norm, right, right_norm)


def _cosine_similarity_with_norms(
    left: Sequence[float], left_norm: float, right: Sequence[float], right_norm: float
) -> float:
    """Same computation as `cosine_similarity`, taking precomputed norms.

    `rank_candidates_by_vector` ranks one reference vector against the whole
    catalog, so the reference's norm is invariant across the loop and every
    track's norm is invariant across every ranking call in a session --
    recomputing either from scratch per candidate is wasted work at scale.
    """
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot_product = sum(a * b for a, b in zip(left, right))
    return dot_product / (left_norm * right_norm)


def _blend_vectors(primary: Sequence[float], secondary: Sequence[float], primary_weight: float) -> list[float]:
    return [primary_weight * a + (1.0 - primary_weight) * b for a, b in zip(primary, secondary)]


def rank_candidates_by_vector(
    reference_vector: Sequence[float],
    catalog: Catalog,
    exclude_track_ids: Collection[str] = (),
) -> list[tuple[TrackRecord, float]]:
    reference_norm = math.sqrt(sum(value * value for value in reference_vector))
    track_norms = catalog.track_norms

    candidates: list[tuple[TrackRecord, float]] = []
    for track in catalog.tracks:
        if not track.enabled or track.id in exclude_track_ids:
            continue
        if not track.feature_vector:
            continue
        right_norm = track_norms.get(track.id)
        if right_norm is None:
            right_norm = math.sqrt(sum(value * value for value in track.feature_vector))
        similarity = _cosine_similarity_with_norms(reference_vector, reference_norm, track.feature_vector, right_norm)
        candidates.append((track, similarity))

    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates


def _trailing_artist_streak(recent_artists: Sequence[str]) -> tuple[str | None, int]:
    if not recent_artists:
        return None, 0
    last_artist = recent_artists[-1]
    streak = 0
    for artist in reversed(recent_artists):
        if artist != last_artist:
            break
        streak += 1
    return last_artist, streak


def _apply_artist_repeat_cap(
    ranked_candidates: Sequence[tuple[TrackRecord, float]],
    recent_artists: Sequence[str],
    max_consecutive_same_artist: int | None,
) -> Sequence[tuple[TrackRecord, float]]:
    """Drop candidates that would extend a same-artist run past the cap.

    A track sharing both genre and artist with the current track scores a
    perfect cosine match, so pure similarity ranking can queue up many
    tracks by the same artist in a row. This is a queue-assembly filter
    (like the existing no-repeat-track exclusion), not a scoring change.
    Falls back to the unfiltered list if the cap would empty the pool
    (e.g. a single-artist library), so playback never stalls.
    """
    if not max_consecutive_same_artist or max_consecutive_same_artist < 1:
        return ranked_candidates
    streak_artist, streak_length = _trailing_artist_streak(recent_artists)
    if streak_artist is None or streak_length < max_consecutive_same_artist:
        return ranked_candidates
    filtered = [item for item in ranked_candidates if item[0].artist != streak_artist]
    return filtered or ranked_candidates


def rank_candidates(
    current_track_id: str,
    catalog: Catalog,
    excluded_track_ids: Collection[str] = (),
) -> list[tuple[TrackRecord, float]]:
    current_track = next((track for track in catalog.tracks if track.id == current_track_id), None)
    if current_track is None:
        raise ValueError(f"Track not found in catalog: {current_track_id}")

    current_vector = current_track.feature_vector
    if not current_vector:
        raise ValueError(f"Track has no feature vector: {current_track_id}")

    return rank_candidates_by_vector(current_vector, catalog, {current_track_id, *excluded_track_ids})


def _sample_weighted_candidates(
    ranked_candidates: Sequence[tuple[TrackRecord, float]],
    randomness: float,
    rng: random.Random,
) -> TrackRecord:
    if not ranked_candidates:
        raise ValueError("No eligible candidates available for recommendation")

    if randomness <= 0:
        return ranked_candidates[0][0]

    clamped_randomness = max(0.0, min(1.0, randomness))
    similarities = [max(score, 0.0) for _, score in ranked_candidates]
    max_similarity = max(similarities, default=0.0)
    if max_similarity == 0.0:
        return rng.choice([track for track, _ in ranked_candidates])

    uniform_weight = clamped_randomness / len(ranked_candidates)
    weights = []
    for similarity in similarities:
        normalized_similarity = similarity / max_similarity
        weight = (1.0 - clamped_randomness) * normalized_similarity + uniform_weight
        weights.append(weight)

    return rng.choices([track for track, _ in ranked_candidates], weights=weights, k=1)[0]


def recommend_next_track(
    current_track_id: str,
    catalog: Catalog,
    top_k: int = 5,
    randomness: float = 0.0,
    rng: random.Random | None = None,
    excluded_track_ids: Collection[str] = (),
    recent_artists: Sequence[str] = (),
    max_consecutive_same_artist: int | None = None,
) -> TrackRecord:
    ranked_candidates = rank_candidates(current_track_id, catalog, excluded_track_ids)
    ranked_candidates = _apply_artist_repeat_cap(ranked_candidates, recent_artists, max_consecutive_same_artist)
    if top_k > 0:
        ranked_candidates = ranked_candidates[:top_k]
    return _sample_weighted_candidates(ranked_candidates, randomness, rng or random.Random())


def recommend_next_track_from_json(
    catalog_path: str | Path,
    current_track_id: str,
    top_k: int = 5,
    randomness: float = 0.0,
    rng: random.Random | None = None,
) -> TrackRecord:
    catalog = load_catalog(catalog_path)
    return recommend_next_track(current_track_id, catalog, top_k=top_k, randomness=randomness, rng=rng)

# Fraction of each ranking step's reference vector drawn from the original
# seed track rather than the previously picked track. Chaining purely off
# the last pick lets small similarity drifts compound step over step, so a
# long queue can end up sounding nothing like what the user started with;
# anchoring part of each step to the seed keeps the whole queue in its
# neighborhood while still allowing gradual progression.
SEED_ANCHOR_WEIGHT = 0.3


def generate_queue_steps(
    current_track_id: str,
    catalog: Catalog,
    length: int = 10,
    top_k: int = 5,
    randomness: float = 0.0,
    rng: random.Random | None = None,
    max_consecutive_same_artist: int | None = 3,
):
    """Same selection logic as `generate_queue`, yielding each track as it's
    picked instead of returning the whole list at once -- lets a caller (the
    TUI) reveal the queue one track at a time instead of all at once.

    Being a generator, validation (length/track lookup) only runs once
    iteration starts; `generate_queue` forces that by consuming it via
    `list(...)`, so its eager-validation behavior is unchanged.
    """
    if length < 1:
        raise ValueError("Queue length must be at least 1")

    seed_track = next((track for track in catalog.tracks if track.id == current_track_id), None)
    if seed_track is None:
        raise ValueError(f"Track not found in catalog: {current_track_id}")
    seed_vector = seed_track.feature_vector
    if not seed_vector:
        raise ValueError(f"Track has no feature vector: {current_track_id}")

    random_generator = rng or random.Random()
    played_track_ids = {current_track_id}
    previous_vector = seed_vector
    recent_artists = [seed_track.artist]

    for _ in range(length):
        reference_vector = _blend_vectors(previous_vector, seed_vector, 1.0 - SEED_ANCHOR_WEIGHT)
        ranked_candidates = rank_candidates_by_vector(reference_vector, catalog, played_track_ids)
        ranked_candidates = _apply_artist_repeat_cap(ranked_candidates, recent_artists, max_consecutive_same_artist)
        if top_k > 0:
            ranked_candidates = ranked_candidates[:top_k]
        if not ranked_candidates:
            break
        next_track = _sample_weighted_candidates(ranked_candidates, randomness, random_generator)
        played_track_ids.add(next_track.id)
        recent_artists.append(next_track.artist)
        previous_vector = next_track.feature_vector
        yield next_track


def generate_queue(
    current_track_id: str,
    catalog: Catalog,
    length: int = 10,
    top_k: int = 5,
    randomness: float = 0.0,
    rng: random.Random | None = None,
    max_consecutive_same_artist: int | None = 3,
) -> list[TrackRecord]:
    return list(
        generate_queue_steps(
            current_track_id,
            catalog,
            length=length,
            top_k=top_k,
            randomness=randomness,
            rng=rng,
            max_consecutive_same_artist=max_consecutive_same_artist,
        )
    )


def generate_queue_from_json(
    catalog_path: str | Path,
    current_track_id: str,
    length: int = 10,
    top_k: int = 5,
    randomness: float = 0.0,
    rng: random.Random | None = None,
    max_consecutive_same_artist: int | None = 3,
) -> list[TrackRecord]:
    catalog = load_catalog(catalog_path)
    return generate_queue(current_track_id, catalog, length, top_k, randomness, rng, max_consecutive_same_artist)
