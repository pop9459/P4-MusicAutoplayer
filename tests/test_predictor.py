from __future__ import annotations

import random
import unittest

from src.predictor import generate_queue
from src.track_analyzer import TrackRecord, build_catalog


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


if __name__ == "__main__":
    unittest.main()
