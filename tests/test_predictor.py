from __future__ import annotations

import random
import unittest
from unittest.mock import patch

import src.predictor as predictor_module
from src.predictor import generate_queue, generate_queue_steps, rank_candidates_by_features
from src.track_analyzer import Catalog, TrackRecord, build_catalog, track_similarity, work_key


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

    def test_generate_queue_matches_generate_queue_steps(self) -> None:
        queue = generate_queue("start", self.catalog, length=3, randomness=0.0, rng=random.Random(1))
        steps = list(generate_queue_steps("start", self.catalog, length=3, randomness=0.0, rng=random.Random(1)))

        self.assertEqual([t.id for t in queue], [t.id for t in steps])

    def test_generate_queue_steps_yields_incrementally(self) -> None:
        steps = generate_queue_steps("start", self.catalog, length=3, randomness=0.0)

        first = next(steps)

        self.assertIsInstance(first, TrackRecord)
        # Exhausting only one step must not have run the whole loop yet --
        # the remaining picks are still available from the same generator.
        remaining = list(steps)
        self.assertEqual(len(remaining), 2)


class QueueSeedAnchoringTests(unittest.TestCase):
    def test_anchoring_prefers_a_pick_closer_to_the_seed_over_continued_drift(self) -> None:
        # Every track shares a genre and has a distinct artist, so release
        # year is the only signal that separates them. Laid out so that at
        # step 2, ranking purely off the previously picked track ("c1")
        # favors drifting further away ("c2"), while blending in the original
        # seed pulls the pick back to "D", which is closer to where the user
        # actually started.
        def mk(track_id: str, year: int) -> TrackRecord:
            return TrackRecord(
                id=track_id, path=f"/{track_id}.mp3", title=track_id, artist=track_id, genre="pop", year=year
            )

        catalog = build_catalog([mk("seed", 2000), mk("c1", 2003), mk("c2", 2009), mk("D", 1996)])

        anchored_queue = generate_queue("seed", catalog, length=2, top_k=1, randomness=0.0)
        with patch.object(predictor_module, "SEED_ANCHOR_WEIGHT", 0.0):
            unanchored_queue = generate_queue("seed", catalog, length=2, top_k=1, randomness=0.0)

        seed_features = catalog.features_for(next(t for t in catalog.tracks if t.id == "seed"))
        anchored_final = track_similarity(seed_features, catalog.features_for(anchored_queue[-1]))
        unanchored_final = track_similarity(seed_features, catalog.features_for(unanchored_queue[-1]))

        self.assertEqual([t.id for t in unanchored_queue], ["c1", "c2"])
        self.assertEqual([t.id for t in anchored_queue], ["c1", "D"])
        self.assertGreater(anchored_final, unanchored_final)


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

    def test_cap_catches_an_alternating_pattern_not_just_a_trailing_run(self) -> None:
        # A dominant artist used to walk straight past a run-length cap:
        # letting another artist take one turn reset the streak, so
        # "A A A B A A A" was never blocked. The cap bounds an artist's
        # share of a window instead.
        queue = generate_queue("acdc0", self.catalog, length=8, top_k=10, randomness=0.0)
        artists = ["ACDC"] + [track.artist for track in queue]

        for start in range(len(artists) - 5):
            window = artists[start : start + 6]
            self.assertLessEqual(window.count("ACDC"), 3)

    def test_cap_falls_back_to_ranking_when_no_alternative_artist_remains(self) -> None:
        single_artist_catalog = build_catalog([
            TrackRecord(id=f"solo{i}", path=f"/solo{i}.mp3", title=f"Solo {i}", artist="Solo", genre="rock", bpm=120 + i, year=1980 + i)
            for i in range(5)
        ])
        # Should not stall/truncate just because every candidate shares an
        # artist with the current streak.
        queue = generate_queue("solo0", single_artist_catalog, length=4, top_k=10, randomness=0.0)
        self.assertEqual(len(queue), 4)


class WorkKeyTests(unittest.TestCase):
    def test_version_markers_resolve_to_the_same_work(self) -> None:
        base = work_key("ACDC", "Thunderstruck")
        self.assertEqual(work_key("ACDC", "Thunderstruck (Live)"), base)
        self.assertEqual(work_key("ACDC", "Thunderstruck - Radio Edit"), base)
        self.assertEqual(work_key("ACDC", "Thunderstruck [Remastered]"), base)

    def test_feat_credits_in_the_title_are_stripped(self) -> None:
        self.assertEqual(
            work_key("Mokaby", "The Passenger (LaLaLa) [feat. MokaBy]"),
            work_key("Mokaby", "The Passenger"),
        )

    def test_lead_artist_decides_the_work_so_collaborations_group(self) -> None:
        self.assertEqual(work_key("Skrillex, Fred again..", "Rumble"), work_key("Skrillex", "Rumble"))

    def test_different_songs_keep_different_keys(self) -> None:
        self.assertNotEqual(work_key("ACDC", "Thunderstruck"), work_key("ACDC", "Back in Black"))
        self.assertNotEqual(work_key("ACDC", "Thunderstruck"), work_key("Queen", "Thunderstruck"))


class WorkKeyCooldownTests(unittest.TestCase):
    def _versions_catalog(self) -> Catalog:
        # Three versions of one song plus enough unrelated-but-similar
        # filler that the queue never has to fall back.
        tracks = [
            TrackRecord(id="orig", path="/o.mp3", title="Thunderstruck", artist="ACDC", genre="rock", year=1990),
            TrackRecord(id="live", path="/l.mp3", title="Thunderstruck (Live)", artist="ACDC", genre="rock", year=1990),
            TrackRecord(id="edit", path="/e.mp3", title="Thunderstruck - Radio Edit", artist="ACDC", genre="rock", year=1990),
        ]
        tracks += [
            TrackRecord(id=f"f{i}", path=f"/f{i}.mp3", title=f"Filler {i}", artist=f"Band {i}", genre="rock", year=1990)
            for i in range(6)
        ]
        return build_catalog(tracks)

    def test_other_versions_of_the_seed_are_not_queued_next(self) -> None:
        queue = generate_queue("orig", self._versions_catalog(), length=6, top_k=10, randomness=0.0)
        self.assertNotIn("live", [t.id for t in queue[:2]])
        self.assertNotIn("edit", [t.id for t in queue[:2]])

    def test_a_queue_never_contains_two_versions_of_one_song(self) -> None:
        catalog = self._versions_catalog()
        # Six distinct works are reachable, so a six-track queue never has
        # to fall back on a repeat.
        queue = generate_queue("orig", catalog, length=6, top_k=10, randomness=0.0)
        keys = [catalog.features_for(track).work_key for track in queue]
        self.assertEqual(len(keys), len(set(keys)))

    def test_cooldown_falls_back_rather_than_truncating_the_queue(self) -> None:
        # A library where every track is a version of the same song must
        # still produce a full queue instead of stalling.
        catalog = build_catalog([
            TrackRecord(id=f"v{i}", path=f"/v{i}.mp3", title=f"Song (Mix {i})", artist="A", genre="pop", year=2000)
            for i in range(5)
        ])
        self.assertEqual(len(generate_queue("v0", catalog, length=4, top_k=10, randomness=0.0)), 4)


class CatalogFeatureCacheTests(unittest.TestCase):
    """`build_catalog` precomputes `Catalog.track_features` so ranking doesn't
    re-derive each track's similarity inputs on every call. The cache is a
    speedup only -- it must never change ranking results, and a catalog built
    without it must still rank identically."""

    def setUp(self) -> None:
        self.catalog = build_catalog([
            TrackRecord(id="start", path="/music/start.mp3", title="Start", artist="A", genre="pop", bpm=120, year=2020),
            TrackRecord(id="one", path="/music/one.mp3", title="One", artist="A", genre="pop", bpm=121, year=2021),
            TrackRecord(id="two", path="/music/two.mp3", title="Two", artist="B", genre="pop", bpm=122, year=2022),
            TrackRecord(id="three", path="/music/three.mp3", title="Three", artist="C", genre="rock", bpm=100, year=2010),
        ])

    def test_build_catalog_populates_features_for_every_track(self) -> None:
        for track in self.catalog.tracks:
            self.assertIn(track.id, self.catalog.track_features)
            self.assertEqual(self.catalog.track_features[track.id].genre, track.genre)

    def test_ranking_matches_direct_similarity_calls(self) -> None:
        seed = self.catalog.features_for(next(t for t in self.catalog.tracks if t.id == "start"))

        ranked = rank_candidates_by_features(seed, self.catalog, {"start"})
        naive = sorted(
            (
                (track, track_similarity(seed, self.catalog.features_for(track)))
                for track in self.catalog.tracks
                if track.id != "start"
            ),
            key=lambda item: item[1],
            reverse=True,
        )

        self.assertEqual([t.id for t, _ in ranked], [t.id for t, _ in naive])
        for (_, ranked_score), (_, naive_score) in zip(ranked, naive):
            self.assertAlmostEqual(ranked_score, naive_score)

    def test_ranking_falls_back_gracefully_when_the_cache_is_missing(self) -> None:
        seed = self.catalog.features_for(next(t for t in self.catalog.tracks if t.id == "start"))
        catalog_without_cache = Catalog(
            version=self.catalog.version,
            tracks=self.catalog.tracks,
            genres=self.catalog.genres,
            artists=self.catalog.artists,
            bpm_min=self.catalog.bpm_min,
            bpm_max=self.catalog.bpm_max,
            year_min=self.catalog.year_min,
            year_max=self.catalog.year_max,
        )

        ranked_with_cache = rank_candidates_by_features(seed, self.catalog, {"start"})
        ranked_without_cache = rank_candidates_by_features(seed, catalog_without_cache, {"start"})

        self.assertEqual([t.id for t, _ in ranked_with_cache], [t.id for t, _ in ranked_without_cache])


class IntentionalPerfectMatchTests(unittest.TestCase):
    """Two tracks whose every compared field is identical score 1.0.

    This is a statement about the data, not an artifact of the scoring: if
    genre, artist, bpm and year all agree, there is nothing left to tell the
    two tracks apart. Repetitive queues from near-identical metadata are
    addressed by top_k/randomness, the artist-repeat cap and the work-key
    cooldown -- queue-assembly filters -- rather than by inventing a
    difference the metadata doesn't contain."""

    def test_identical_metadata_scores_perfect_similarity(self) -> None:
        catalog = build_catalog([
            TrackRecord(id="a1", path="/a1.mp3", title="A1", artist="Same Artist", genre="pop", bpm=120, year=2020),
            TrackRecord(id="a2", path="/a2.mp3", title="A2", artist="Same Artist", genre="pop", bpm=120, year=2020),
            TrackRecord(id="b1", path="/b1.mp3", title="B1", artist="Other Artist", genre="rock", bpm=90, year=1995),
        ])
        a1 = catalog.features_for(next(t for t in catalog.tracks if t.id == "a1"))
        a2 = catalog.features_for(next(t for t in catalog.tracks if t.id == "a2"))

        self.assertAlmostEqual(track_similarity(a1, a2), 1.0)


if __name__ == "__main__":
    unittest.main()
