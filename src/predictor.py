from __future__ import annotations

import random
from collections import Counter
from typing import Collection, Sequence

from .track_analyzer import Catalog, TrackFeatures, TrackRecord, track_similarity


def rank_candidates_by_features(
    reference: TrackFeatures,
    catalog: Catalog,
    exclude_track_ids: Collection[str] = (),
    anchor: TrackFeatures | None = None,
    anchor_weight: float = 0.0,
) -> list[tuple[TrackRecord, float]]:
    """Rank every eligible track against `reference`, best first.

    When `anchor` is given, each candidate's score is blended with its
    similarity to the anchor. This replaces blending the two reference
    *vectors* together, which produced a synthetic reference no real track
    could have -- averaging two one-hot artist encodings described a track
    that is "70% artist X, 30% artist Y". Blending the two scores instead
    asks a question each track can actually answer.
    """
    blend = max(0.0, min(1.0, anchor_weight)) if anchor is not None else 0.0

    candidates: list[tuple[TrackRecord, float]] = []
    for track in catalog.tracks:
        if not track.enabled or track.id in exclude_track_ids:
            continue
        features = catalog.features_for(track)
        score = track_similarity(reference, features)
        if blend > 0.0:
            score = (1.0 - blend) * score + blend * track_similarity(anchor, features)
        candidates.append((track, score))

    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates


def _find_track(catalog: Catalog, track_id: str) -> TrackRecord:
    track = next((item for item in catalog.tracks if item.id == track_id), None)
    if track is None:
        raise ValueError(f"Track not found in catalog: {track_id}")
    return track


def _apply_artist_repeat_cap(
    ranked_candidates: Sequence[tuple[TrackRecord, float]],
    recent_artists: Sequence[str],
    max_consecutive_same_artist: int | None,
) -> Sequence[tuple[TrackRecord, float]]:
    """Drop candidates whose artist already fills the recent window.

    A track sharing genre and artist with the current one scores a near
    perfect match, so pure similarity ranking queues up one artist at a
    time. The cap used to look only at the trailing run, which a dominant
    artist walks straight past: with a cap of 3, "A A A B B B A A A" is
    never blocked, because the run resets every time another artist gets a
    turn. Counting occurrences across a window instead catches that.

    The window is twice the cap, so the cap is a share of recent picks
    rather than a run length. This is a queue-assembly filter (like the
    no-repeat-track exclusion), not a scoring change, and it falls back to
    the unfiltered list if it would empty the pool -- a single-artist
    library must still play.
    """
    if not max_consecutive_same_artist or max_consecutive_same_artist < 1:
        return ranked_candidates
    window = recent_artists[-(2 * max_consecutive_same_artist) :]
    if not window:
        return ranked_candidates
    counts = Counter(window)
    saturated = {artist for artist, count in counts.items() if count >= max_consecutive_same_artist}
    if not saturated:
        return ranked_candidates
    filtered = [item for item in ranked_candidates if item[0].artist not in saturated]
    return filtered or ranked_candidates


def _apply_work_key_cooldown(
    ranked_candidates: Sequence[tuple[TrackRecord, float]],
    catalog: Catalog,
    recent_work_keys: Collection[str],
) -> Sequence[tuple[TrackRecord, float]]:
    """Drop candidates that are another version of a recently picked song.

    Overlapping source folders mean the same song is often present several
    times over, and a remix is a legitimately different track that must stay
    in the library -- but both score near-identical metadata similarity to
    the original, so ranking alone plays them back to back. This suppresses
    a work only while it is recent; it stays reachable later in the session.

    A queue-assembly filter like `_apply_artist_repeat_cap`, with the same
    fall back to the unfiltered list so playback never stalls.
    """
    if not recent_work_keys:
        return ranked_candidates
    blocked = set(recent_work_keys)
    filtered = [item for item in ranked_candidates if catalog.features_for(item[0]).work_key not in blocked]
    return filtered or ranked_candidates


def rank_candidates(
    current_track_id: str,
    catalog: Catalog,
    excluded_track_ids: Collection[str] = (),
) -> list[tuple[TrackRecord, float]]:
    current_track = _find_track(catalog, current_track_id)
    return rank_candidates_by_features(
        catalog.features_for(current_track),
        catalog,
        {current_track_id, *excluded_track_ids},
    )


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
    recent_work_keys: Collection[str] = (),
) -> TrackRecord:
    ranked_candidates = rank_candidates(current_track_id, catalog, excluded_track_ids)
    ranked_candidates = _apply_work_key_cooldown(ranked_candidates, catalog, recent_work_keys)
    ranked_candidates = _apply_artist_repeat_cap(ranked_candidates, recent_artists, max_consecutive_same_artist)
    if top_k > 0:
        ranked_candidates = ranked_candidates[:top_k]
    return _sample_weighted_candidates(ranked_candidates, randomness, rng or random.Random())


# Fraction of each ranking step's score drawn from similarity to the original
# seed track rather than to the previously picked track. Chaining purely off
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

    seed_track = _find_track(catalog, current_track_id)
    seed_features = catalog.features_for(seed_track)

    random_generator = rng or random.Random()
    played_track_ids = {current_track_id}
    previous_features = seed_features
    recent_artists = [seed_track.artist]
    # One generated queue is itself the cooldown window: no song appears
    # twice in it, in any version.
    recent_work_keys = [seed_features.work_key]

    for _ in range(length):
        ranked_candidates = rank_candidates_by_features(
            previous_features,
            catalog,
            played_track_ids,
            anchor=seed_features,
            anchor_weight=SEED_ANCHOR_WEIGHT,
        )
        ranked_candidates = _apply_work_key_cooldown(ranked_candidates, catalog, recent_work_keys)
        ranked_candidates = _apply_artist_repeat_cap(ranked_candidates, recent_artists, max_consecutive_same_artist)
        if top_k > 0:
            ranked_candidates = ranked_candidates[:top_k]
        if not ranked_candidates:
            break
        next_track = _sample_weighted_candidates(ranked_candidates, randomness, random_generator)
        played_track_ids.add(next_track.id)
        recent_artists.append(next_track.artist)
        next_features = catalog.features_for(next_track)
        recent_work_keys.append(next_features.work_key)
        previous_features = next_features
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
