from __future__ import annotations

import math
import random
import unittest
from unittest.mock import patch

import src.predictor as predictor_module
from src.predictor import cosine_similarity, generate_queue
from src.track_analyzer import Catalog, TrackRecord, build_catalog


class QueueGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_catalog([
            TrackRecord(id="start", path="/music/start.mp3", title="Start", artist="A", genre="pop", bpm=120, year=2020),
            TrackRecord(id="one", path="/music/one.mp3", title="One", artist="A", genre="pop", bpm=121, year=2021),
            TrackRecord(id="two", path="/music/two.mp3", title="Two", artist="B", genre="pop", bpm=122, year=2022),
            TrackRecord(id="three", path="/music/three.mp3", title="Three", artist="C", genre="rock", bpm=100, year=2010),
            TrackRecord(id="disabled", path="/music/disabled.mp3", title="Disabled", artist="A", genre="pop", bpm=120, year=2020, enabled=False),
        ])

    def test_queue_chains_without_repeating_tracks(self) -> None:
        queue = generate_queue("start", self.catalog, length=3, randomness=0.0)
        queue_ids = [track.id for track in queue]

        self.assertEqual(len(queue_ids), 3)
        self.assertNotIn("start", queue_ids)
        self.assertNotIn("disabled", queue_ids)
        self.assertEqual(len(queue_ids), len(set(queue_ids)))

    def test_queue_stops_when_unseen_eligible_tracks_are_exhausted(self) -> None:
        queue = generate_queue("start", self.catalog, length=10, randomness=1.0, rng=random.Random(7))

        self.assertEqual(len(queue), 3)
        self.assertEqual({track.id for track in queue}, {"one", "two", "three"})


class QueueSeedAnchoringTests(unittest.TestCase):
    def test_anchoring_prefers_a_pick_closer_to_the_seed_over_continued_drift(self) -> None:
        # Synthetic 2D feature vectors (angles on the unit circle) built to
        # demonstrate seed anchoring: at step 2, ranking purely off the
        # previously picked track ("c1") favors continuing to drift further
        # away ("c2"), while blending in the original seed pulls the pick
        # back toward a track ("D") that is actually more similar to the
        # seed the user started from.
        def vec(degrees: float) -> list[float]:
            radians = math.radians(degrees)
            return [math.cos(radians), math.sin(radians)]

        def mk(track_id: str, degrees: float) -> TrackRecord:
            return TrackRecord(id=track_id, path=f"/{track_id}.mp3", title=track_id, feature_vector=vec(degrees))

        tracks = [mk("seed", 0), mk("c1", 15), mk("c2", 50), mk("D", -25)]
        catalog = Catalog(
            version=1, tracks=tracks, genres=[], artists=[],
            bpm_min=None, bpm_max=None, year_min=None, year_max=None,
        )

        anchored_queue = generate_queue("seed", catalog, length=2, top_k=1, randomness=0.0)
        with patch.object(predictor_module, "SEED_ANCHOR_WEIGHT", 0.0):
            unanchored_queue = generate_queue("seed", catalog, length=2, top_k=1, randomness=0.0)

        seed_vector = vec(0)
        anchored_final_similarity = cosine_similarity(seed_vector, anchored_queue[-1].feature_vector)
        unanchored_final_similarity = cosine_similarity(seed_vector, unanchored_queue[-1].feature_vector)

        self.assertEqual([t.id for t in unanchored_queue], ["c1", "c2"])
        self.assertEqual([t.id for t in anchored_queue], ["c1", "D"])
        self.assertGreater(anchored_final_similarity, unanchored_final_similarity)


def _max_consecutive_run(values: list[str]) -> int:
    longest = 0
    current = 0
    previous = None
    for value in values:
        current = current + 1 if value == previous else 1
        longest = max(longest, current)
        previous = value
    return longest


class ArtistRepeatCapTests(unittest.TestCase):
    def setUp(self) -> None:
        # One artist dominates the library (ACDC), so pure similarity
        # ranking would otherwise queue up many of its tracks in a row.
        acdc_tracks = [
            TrackRecord(id=f"acdc{i}", path=f"/acdc{i}.mp3", title=f"ACDC {i}", artist="ACDC", genre="rock", bpm=120 + i, year=1980 + i)
            for i in range(6)
        ]
        other_tracks = [
            TrackRecord(id="queenA", path="/queenA.mp3", title="Queen A", artist="Queen", genre="rock", bpm=121, year=1981),
            TrackRecord(id="queenB", path="/queenB.mp3", title="Queen B", artist="Queen", genre="rock", bpm=122, year=1982),
            TrackRecord(id="kissA", path="/kissA.mp3", title="Kiss A", artist="Kiss", genre="rock", bpm=123, year=1983),
        ]
        self.catalog = build_catalog(acdc_tracks + other_tracks)

    def test_default_cap_prevents_more_than_three_in_a_row(self) -> None:
        queue = generate_queue("acdc0", self.catalog, length=8, top_k=10, randomness=0.0)
        artists = [track.artist for track in queue]

        self.assertLessEqual(_max_consecutive_run(artists), 3)

    def test_disabling_the_cap_reproduces_uncapped_behavior(self) -> None:
        queue = generate_queue("acdc0", self.catalog, length=8, top_k=10, randomness=0.0, max_consecutive_same_artist=None)
        artists = [track.artist for track in queue]

        # Without a cap, pure similarity ranking exhausts every remaining
        # ACDC track before ever picking a different artist.
        self.assertGreater(_max_consecutive_run(artists), 3)

    def test_cap_falls_back_to_ranking_when_no_alternative_artist_remains(self) -> None:
        single_artist_catalog = build_catalog([
            TrackRecord(id=f"solo{i}", path=f"/solo{i}.mp3", title=f"Solo {i}", artist="Solo", genre="rock", bpm=120 + i, year=1980 + i)
            for i in range(5)
        ])
        # Should not stall/truncate just because every candidate shares an
        # artist with the current streak.
        queue = generate_queue("solo0", single_artist_catalog, length=4, top_k=10, randomness=0.0)
        self.assertEqual(len(queue), 4)


if __name__ == "__main__":
    unittest.main()
