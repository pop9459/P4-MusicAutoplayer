from __future__ import annotations

import random
import unittest

from src.player import PlayerEngine, clamp_index, filter_enabled_tracks, format_track_line
from src.track_analyzer import TrackRecord, build_catalog


class PureHelperTests(unittest.TestCase):
    def test_clamp_index_within_range_is_unchanged(self) -> None:
        self.assertEqual(clamp_index(2, 5), 2)

    def test_clamp_index_clamps_low_and_high(self) -> None:
        self.assertEqual(clamp_index(-3, 5), 0)
        self.assertEqual(clamp_index(99, 5), 4)

    def test_clamp_index_empty_list_is_zero(self) -> None:
        self.assertEqual(clamp_index(0, 0), 0)

    def test_format_track_line(self) -> None:
        track = TrackRecord(id="t", path="/music/t.mp3", title="Title", artist="Artist")
        self.assertEqual(format_track_line(track), "Artist - Title")

    def test_filter_enabled_tracks_excludes_disabled(self) -> None:
        catalog = build_catalog([
            TrackRecord(id="a", path="/music/a.mp3", title="A", artist="A", enabled=True),
            TrackRecord(id="b", path="/music/b.mp3", title="B", artist="B", enabled=False),
        ])
        enabled = filter_enabled_tracks(catalog)
        self.assertEqual([track.id for track in enabled], ["a"])


class PlayerEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = build_catalog([
            TrackRecord(id="start", path="/music/start.mp3", title="Start", artist="A", genre="pop", bpm=120, year=2020),
            TrackRecord(id="one", path="/music/one.mp3", title="One", artist="A", genre="pop", bpm=121, year=2021),
            TrackRecord(id="two", path="/music/two.mp3", title="Two", artist="B", genre="pop", bpm=122, year=2022),
            TrackRecord(id="three", path="/music/three.mp3", title="Three", artist="C", genre="rock", bpm=100, year=2010),
        ])
        self.start_track = next(track for track in self.catalog.tracks if track.id == "start")

    def test_initial_queue_is_generated_on_construction(self) -> None:
        engine = PlayerEngine(self.catalog, self.start_track, top_k=5, randomness=0.0, queue_length=3)
        self.assertEqual(len(engine.queue), 3)
        self.assertEqual(engine.current_track.id, "start")

    def test_advance_pops_queue_and_updates_history(self) -> None:
        engine = PlayerEngine(self.catalog, self.start_track, top_k=5, randomness=0.0, queue_length=3)
        first_queued = engine.peek_next()

        next_track = engine.advance()

        self.assertIsNotNone(next_track)
        self.assertEqual(next_track.id, first_queued.id)
        self.assertEqual(engine.current_track.id, first_queued.id)
        self.assertEqual([t.id for t in engine.history], ["start", first_queued.id])

    def test_advance_tops_queue_back_up_when_unseen_tracks_remain(self) -> None:
        # Bigger catalog than queue_length so a top-up has somewhere to pull
        # a fresh, not-yet-played track from after each advance.
        catalog = build_catalog([
            TrackRecord(id="start", path="/music/start.mp3", title="Start", artist="A", genre="pop", bpm=120, year=2020),
            *(
                TrackRecord(id=f"track{i}", path=f"/music/track{i}.mp3", title=f"Track {i}", artist="A", genre="pop", bpm=120 + i, year=2020 + i)
                for i in range(1, 7)
            ),
        ])
        start_track = next(track for track in catalog.tracks if track.id == "start")
        engine = PlayerEngine(catalog, start_track, top_k=5, randomness=0.0, queue_length=3)
        self.assertEqual(len(engine.queue), 3)

        engine.advance()

        # Queue should be topped back up to full length, not left at 2.
        self.assertEqual(len(engine.queue), 3)
        self.assertTrue(engine.queue_regenerated)
        # No track should repeat between history and the current queue.
        seen_ids = [t.id for t in engine.history] + [t.id for t in engine.queue]
        self.assertEqual(len(seen_ids), len(set(seen_ids)))

    def test_advance_stops_topping_up_once_catalog_is_exhausted(self) -> None:
        # This catalog only has exactly queue_length unique non-start tracks,
        # so once they've all been played or queued, no unseen candidate
        # remains and the queue can no longer be topped up.
        engine = PlayerEngine(self.catalog, self.start_track, top_k=5, randomness=0.0, queue_length=3)

        engine.advance()
        self.assertFalse(engine.queue_regenerated)
        self.assertEqual(len(engine.queue), 2)

        engine.advance()
        self.assertFalse(engine.queue_regenerated)
        self.assertEqual(len(engine.queue), 1)

        engine.advance()
        self.assertFalse(engine.queue_regenerated)
        self.assertEqual(engine.queue, [])

        self.assertIsNone(engine.advance())

    def test_advance_returns_none_when_no_eligible_tracks_remain(self) -> None:
        tiny_catalog = build_catalog([
            TrackRecord(id="only", path="/music/only.mp3", title="Only", artist="A"),
        ])
        only_track = tiny_catalog.tracks[0]
        engine = PlayerEngine(tiny_catalog, only_track, top_k=5, randomness=0.0, queue_length=3)

        self.assertEqual(engine.queue, [])
        self.assertIsNone(engine.advance())


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
        # One artist dominates the library, so both the initial queue
        # (generate_queue, via _refill_if_needed) and later top-ups
        # (recommend_next_track, via _top_up_queue) would otherwise queue
        # up many of its tracks in a row.
        acdc_tracks = [
            TrackRecord(id=f"acdc{i}", path=f"/acdc{i}.mp3", title=f"ACDC {i}", artist="ACDC", genre="rock", bpm=120 + i, year=1980 + i)
            for i in range(10)
        ]
        other_tracks = [
            TrackRecord(id="queenA", path="/queenA.mp3", title="Queen A", artist="Queen", genre="rock", bpm=121, year=1981),
            TrackRecord(id="queenB", path="/queenB.mp3", title="Queen B", artist="Queen", genre="rock", bpm=122, year=1982),
            TrackRecord(id="kissA", path="/kissA.mp3", title="Kiss A", artist="Kiss", genre="rock", bpm=123, year=1983),
        ]
        self.catalog = build_catalog(acdc_tracks + other_tracks)
        self.start_track = next(track for track in self.catalog.tracks if track.id == "acdc0")

    def test_cap_holds_across_refill_and_top_up(self) -> None:
        engine = PlayerEngine(self.catalog, self.start_track, top_k=10, randomness=0.0, queue_length=4)
        self.assertLessEqual(_max_consecutive_run([t.artist for t in engine.queue]), 3)

        for _ in range(6):
            engine.advance()
            timeline = [self.start_track.artist] + [t.artist for t in engine.history[1:]] + [t.artist for t in engine.queue]
            self.assertLessEqual(_max_consecutive_run(timeline), 3)


if __name__ == "__main__":
    unittest.main()
