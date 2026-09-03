from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Collection, Sequence

from .track_analyzer import Catalog, TrackRecord, load_catalog


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Feature vectors must have the same length")

    dot_product = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot_product / (left_norm * right_norm)


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

    candidates: list[tuple[TrackRecord, float]] = []
    for track in catalog.tracks:
        if not track.enabled or track.id == current_track_id or track.id in excluded_track_ids:
            continue
        if not track.feature_vector:
            continue
        similarity = cosine_similarity(current_vector, track.feature_vector)
        candidates.append((track, similarity))

    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates


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
) -> TrackRecord:
    ranked_candidates = rank_candidates(current_track_id, catalog, excluded_track_ids)
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

def generate_queue(
    current_track_id: str,
    catalog: Catalog,
    length: int = 10,
    top_k: int = 5,
    randomness: float = 0.0,
    rng: random.Random | None = None,
) -> list[TrackRecord]:
    if length < 1:
        raise ValueError("Queue length must be at least 1")

    random_generator = rng or random.Random()
    played_track_ids = {current_track_id}
    queue: list[TrackRecord] = []
    previous_track_id = current_track_id

    for _ in range(length):
        ranked_candidates = rank_candidates(previous_track_id, catalog, played_track_ids)
        if top_k > 0:
            ranked_candidates = ranked_candidates[:top_k]
        if not ranked_candidates:
            break
        next_track = _sample_weighted_candidates(ranked_candidates, randomness, random_generator)
        queue.append(next_track)
        played_track_ids.add(next_track.id)
        previous_track_id = next_track.id

    return queue


def generate_queue_from_json(
    catalog_path: str | Path,
    current_track_id: str,
    length: int = 10,
    top_k: int = 5,
    randomness: float = 0.0,
    rng: random.Random | None = None,
) -> list[TrackRecord]:
    catalog = load_catalog(catalog_path)
    return generate_queue(current_track_id, catalog, length, top_k, randomness, rng)
