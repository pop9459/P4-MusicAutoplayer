"""Tests for tools/eval_holdout.py.

Asserts the retrieval arithmetic on hand-computable cases and the invariants
that keep the tool honest -- weight restoration, singleton-album exclusion,
the same-artist pool guard. The numbers the tool reports on a real library are
what it exists to observe, so those are deliberately not pinned (the same
reasoning as tests/test_recommender_report.py).
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.library import Library, LibraryFolder, save_library
from src.track_analyzer import FEATURE_WEIGHTS, TrackFeatures, TrackRecord, build_catalog
from tools.eval_holdout import (
    ablation_weights,
    build_album_pairs,
    evaluate_retrieval,
    feature_weights,
    main,
)

_SETTINGS = {"version": 1, "top_k": 5, "randomness": 0.15, "queue_length": 10}


def _track(index: int, album: str, artist: str = "Artist", **overrides) -> TrackRecord:
    defaults = dict(
        id=f"t{index}",
        path=f"/music/t{index}.mp3",
        title=f"Track {index}",
        artist=artist,
        album=album,
        genre="rock",
        bpm=120.0,
        year=2000,
        folder_id="f1",
    )
    return TrackRecord(**(defaults | overrides))


def _catalog(tracks):
    return build_catalog(tracks)


class BuildAlbumPairsTests(unittest.TestCase):
    def test_album_mates_are_mutual_and_exclude_the_track_itself(self) -> None:
        catalog = _catalog([_track(0, "A"), _track(1, "A"), _track(2, "A")])

        mates = build_album_pairs(catalog)

        self.assertEqual(mates["t0"], {"t1", "t2"})
        self.assertEqual(mates["t1"], {"t0", "t2"})

    def test_singleton_albums_are_dropped(self) -> None:
        catalog = _catalog([_track(0, "Alone"), _track(1, "Pair"), _track(2, "Pair")])

        mates = build_album_pairs(catalog)

        self.assertNotIn("t0", mates)
        self.assertEqual(set(mates), {"t1", "t2"})

    def test_an_empty_album_tag_is_not_an_album(self) -> None:
        catalog = _catalog([_track(0, ""), _track(1, ""), _track(2, "")])

        self.assertEqual(build_album_pairs(catalog), {})

    def test_album_matching_ignores_case(self) -> None:
        catalog = _catalog([_track(0, "Greatest Hits"), _track(1, "greatest hits")])

        self.assertEqual(build_album_pairs(catalog)["t0"], {"t1"})

    def test_disabled_tracks_are_not_ground_truth(self) -> None:
        catalog = _catalog([_track(0, "A"), _track(1, "A", enabled=False)])

        self.assertEqual(build_album_pairs(catalog), {})


class EvaluateRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        # t0/t1 are album-mates; t2..t5 are distractors in their own albums.
        self.catalog = _catalog(
            [_track(0, "A"), _track(1, "A")] + [_track(i, f"Other {i}") for i in range(2, 6)]
        )
        self.mates = build_album_pairs(self.catalog)

    def _score_by_id(self, order: list[str]):
        """Scorer that ranks tracks by their position in `order`, best first."""
        rank = {track_id: -index for index, track_id in enumerate(order)}
        by_features = {
            id(self.catalog.features_for(track)): track.id for track in self.catalog.tracks
        }

        def score(_reference: TrackFeatures, candidate: TrackFeatures) -> float:
            return rank.get(by_features[id(candidate)], -99)

        return score

    def test_a_perfect_scorer_scores_one(self) -> None:
        result = evaluate_retrieval(
            self.catalog, self.mates, self._score_by_id(["t1", "t0"]), "perfect"
        )

        self.assertEqual(result.queries, 2)
        self.assertEqual(result.precision_at_1, 1.0)
        self.assertEqual(result.mrr, 1.0)

    def test_a_mate_ranked_second_gives_half_reciprocal_rank(self) -> None:
        # Every query ranks a distractor first and its mate second.
        order = ["t2", "t1", "t0", "t3", "t4", "t5"]
        result = evaluate_retrieval(self.catalog, self.mates, self._score_by_id(order), "second")

        self.assertEqual(result.precision_at_1, 0.0)
        self.assertAlmostEqual(result.mrr, 0.5)
        self.assertEqual(result.recall_at_10, 1.0)

    def test_queries_without_a_mate_are_not_counted(self) -> None:
        result = evaluate_retrieval(
            self.catalog,
            self.mates,
            self._score_by_id(["t1", "t0"]),
            "subset",
            queries=[track for track in self.catalog.tracks],
        )

        # Six tracks passed in, but only the two with album-mates are scorable.
        self.assertEqual(result.queries, 2)

    def test_same_artist_only_skips_queries_whose_pool_is_too_small(self) -> None:
        # t0/t1 share an album but their artist has only those two tracks, so
        # the same-artist pool is one candidate -- far too small to score.
        catalog = _catalog(
            [_track(0, "A", artist="Duo"), _track(1, "A", artist="Duo")]
            + [_track(i, f"Other {i}", artist="Crowd") for i in range(2, 8)]
        )
        mates = build_album_pairs(catalog)

        result = evaluate_retrieval(
            catalog, mates, lambda a, b: 1.0, "tiny pool", same_artist_only=True
        )

        self.assertEqual(result.queries, 0)

    def test_an_empty_ground_truth_reports_zero_rather_than_dividing_by_zero(self) -> None:
        result = evaluate_retrieval(self.catalog, {}, lambda a, b: 1.0, "empty")

        self.assertEqual(result.queries, 0)
        self.assertEqual(result.mrr, 0.0)


class FeatureWeightsTests(unittest.TestCase):
    def test_weights_are_restored_on_exit(self) -> None:
        original = dict(FEATURE_WEIGHTS)

        with feature_weights({"bpm": 0.0}):
            self.assertEqual(FEATURE_WEIGHTS["bpm"], 0.0)

        self.assertEqual(FEATURE_WEIGHTS, original)

    def test_weights_are_restored_when_the_body_raises(self) -> None:
        original = dict(FEATURE_WEIGHTS)

        with self.assertRaises(RuntimeError):
            with feature_weights({"genre": 0.0}):
                raise RuntimeError("ablation row blew up")

        self.assertEqual(FEATURE_WEIGHTS, original)

    def test_the_table_is_patched_in_place_so_track_similarity_sees_it(self) -> None:
        # track_similarity reads the module-level dict per call, so the patch
        # has to mutate that object rather than rebind a name.
        with feature_weights({"bpm": 0.0}):
            from src.track_analyzer import FEATURE_WEIGHTS as live

            self.assertEqual(live["bpm"], 0.0)


class AblationWeightsTests(unittest.TestCase):
    def test_current_is_first_so_every_row_is_read_against_it(self) -> None:
        rows = ablation_weights()

        self.assertEqual(rows[0][0], "current")
        self.assertEqual(rows[0][1], dict(FEATURE_WEIGHTS))

    def test_every_term_gets_a_leave_one_out_and_an_only_row(self) -> None:
        labels = [label for label, _ in ablation_weights()]

        for term in FEATURE_WEIGHTS:
            self.assertIn(f"without {term}", labels)
            self.assertIn(f"{term} only", labels)

    def test_a_leave_one_out_row_zeroes_exactly_one_term(self) -> None:
        rows = dict(ablation_weights())

        self.assertEqual(rows["without bpm"]["bpm"], 0.0)
        self.assertEqual(rows["without bpm"]["genre"], FEATURE_WEIGHTS["genre"])


def _library(tracks) -> Library:
    folder = LibraryFolder(
        id="f1", path="/music", display_name="music", added_at="t", last_scanned_at="t",
        track_count=len(tracks),
    )
    return Library(version=1, folders=[folder], catalog=build_catalog(tracks))


class MainTests(unittest.TestCase):
    def _run(self, tracks, *extra: str) -> tuple[int, str]:
        with TemporaryDirectory() as directory:
            library_path = Path(directory) / "library.json"
            settings_path = Path(directory) / "settings.json"
            save_library(_library(tracks), library_path)
            settings_path.write_text(json.dumps(_SETTINGS | {"library_path": str(library_path)}))

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--library", str(library_path), "--settings", str(settings_path), *extra])
            return code, output.getvalue()

    def _varied_tracks(self, count: int = 24):
        genres = ["rock", "pop", "electronic"]
        return [
            _track(
                i,
                album=f"Album {i // 3}",
                artist=f"Artist {i // 3}",
                genre=genres[i % len(genres)],
                bpm=90.0 + i,
                year=1990 + i,
            )
            for i in range(count)
        ]

    def test_every_section_is_reported(self) -> None:
        code, output = self._run(self._varied_tracks())

        self.assertEqual(code, 0)
        self.assertIn("Ground truth:", output)
        self.assertIn("Album-mate retrieval", output)
        self.assertIn("random baseline", output)
        self.assertIn("Term coherence", output)

    def test_the_baseline_is_always_reported_next_to_the_result(self) -> None:
        # A retrieval number without its floor is not a measurement, so the
        # baseline must survive even the shortest run.
        _, output = self._run(self._varied_tracks(), "--no-ablation", "--limit", "4")

        self.assertIn("current similarity", output)
        self.assertIn("random baseline", output)

    def test_the_ablation_table_can_be_skipped(self) -> None:
        _, with_table = self._run(self._varied_tracks())
        _, without_table = self._run(self._varied_tracks(), "--no-ablation")

        self.assertIn("Leave-one-out", with_table)
        self.assertNotIn("Leave-one-out", without_table)

    def test_same_artist_only_labels_its_scope(self) -> None:
        _, output = self._run(self._varied_tracks(), "--same-artist-only", "--no-ablation")

        self.assertIn("same-artist pool", output)

    def test_runs_are_deterministic_so_two_reports_can_be_compared(self) -> None:
        tracks = self._varied_tracks()
        arguments = ("--no-ablation", "--seed", "7")

        def measurements(report: str) -> str:
            return report.split("\n", 1)[1]

        _, first = self._run(tracks, *arguments)
        _, second = self._run(tracks, *arguments)

        self.assertEqual(measurements(first), measurements(second))

    def test_the_weight_table_survives_a_full_run(self) -> None:
        original = dict(FEATURE_WEIGHTS)

        self._run(self._varied_tracks())

        self.assertEqual(FEATURE_WEIGHTS, original)

    def test_an_empty_library_is_reported_rather_than_crashing(self) -> None:
        code, output = self._run([])

        self.assertEqual(code, 1)
        self.assertIn("No enabled tracks", output)

    def test_a_library_with_no_album_mates_says_so_rather_than_reporting_zeroes(self) -> None:
        tracks = [_track(i, album=f"Single {i}") for i in range(6)]

        code, output = self._run(tracks)

        self.assertEqual(code, 1)
        self.assertIn("No album-mates", output)


if __name__ == "__main__":
    unittest.main()
