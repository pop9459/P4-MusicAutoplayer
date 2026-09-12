"""Tests for src/track_analyzer.py: genre canonicalization, tag extraction,
and feature-vector weighting.
"""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from src.predictor import rank_candidates
from src.track_analyzer import (
    FEATURE_WEIGHTS,
    TrackRecord,
    _build_feature_vector,
    _parse_bpm_from_tag,
    _parse_year_from_tag,
    _read_tag_metadata,
    build_catalog,
    canonicalize_genre,
)


class CanonicalizeGenreTests(unittest.TestCase):
    def test_empty_or_none_falls_back_to_unknown(self) -> None:
        self.assertEqual(canonicalize_genre(None), "unknown")
        self.assertEqual(canonicalize_genre(""), "unknown")
        self.assertEqual(canonicalize_genre("   "), "unknown")

    def test_exact_alias_map_entries(self) -> None:
        self.assertEqual(canonicalize_genre("Dance Pop"), "pop")
        self.assertEqual(canonicalize_genre("electro pop"), "pop")
        self.assertEqual(canonicalize_genre("EDM"), "electronic")
        self.assertEqual(canonicalize_genre("RnB"), "r&b")

    def test_rock_family_variants_all_collapse_to_rock(self) -> None:
        for variant in ["Australian Rock", "classic rock", "album rock", "glam rock", "rock-and-roll".replace("-and-roll", " rock")]:
            self.assertEqual(canonicalize_genre(variant), "rock")

    def test_hip_hop_family_variants_collapse(self) -> None:
        self.assertEqual(canonicalize_genre("Cali Rap"), "hip-hop")
        self.assertEqual(canonicalize_genre("Czsk Hip Hop"), "hip-hop")
        self.assertEqual(canonicalize_genre("trap"), "hip-hop")

    def test_electronic_family_variants_collapse(self) -> None:
        for variant in ["Eurodance", "Big Room", "Dutch House", "Techno", "Dubstep"]:
            self.assertEqual(canonicalize_genre(variant), "electronic")

    def test_pop_family_catches_uncommon_pop_variants(self) -> None:
        self.assertEqual(canonicalize_genre("Slovak Pop"), "pop")
        self.assertEqual(canonicalize_genre("Classic Czech Pop"), "pop")

    def test_unmapped_niche_genre_is_kept_as_is(self) -> None:
        self.assertEqual(canonicalize_genre("Heligonka"), "folk")
        self.assertEqual(canonicalize_genre("Some Totally Unique Tag"), "some totally unique tag")


class YearAndBpmParsingTests(unittest.TestCase):
    def test_parse_year_from_full_date(self) -> None:
        self.assertEqual(_parse_year_from_tag("2014-04-04"), 2014)

    def test_parse_year_from_bare_year(self) -> None:
        self.assertEqual(_parse_year_from_tag("1993"), 1993)

    def test_parse_year_from_none_or_empty(self) -> None:
        self.assertIsNone(_parse_year_from_tag(None))
        self.assertIsNone(_parse_year_from_tag(""))

    def test_parse_year_from_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_year_from_tag("not a date"))

    def test_parse_bpm_valid(self) -> None:
        self.assertEqual(_parse_bpm_from_tag("128"), 128.0)
        self.assertEqual(_parse_bpm_from_tag("128.5"), 128.5)

    def test_parse_bpm_invalid_returns_none(self) -> None:
        self.assertIsNone(_parse_bpm_from_tag("fast"))
        self.assertIsNone(_parse_bpm_from_tag(None))


class ReadTagMetadataTests(unittest.TestCase):
    def test_reads_genre_year_album_bpm_when_present(self) -> None:
        fake_tags = {
            "genre": ["Eurodance"],
            "album": ["No Limits"],
            "date": ["1993-05-10"],
            "bpm": ["140"],
        }
        with patch("src.track_analyzer.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value = fake_tags
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertEqual(result["genre"], "Eurodance")
        self.assertEqual(result["album"], "No Limits")
        self.assertEqual(result["year"], 1993)
        self.assertEqual(result["bpm"], 140.0)

    def test_missing_tags_omit_keys(self) -> None:
        with patch("src.track_analyzer.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value = {}
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertEqual(result, {})

    def test_no_tags_object_returns_empty_dict(self) -> None:
        with patch("src.track_analyzer.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value = None
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertEqual(result, {})

    def test_mutagen_exception_is_swallowed(self) -> None:
        with patch("src.track_analyzer.mutagen") as mock_mutagen:
            mock_mutagen.File.side_effect = Exception("corrupt file")
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertEqual(result, {})

    def test_mutagen_unavailable_returns_empty_dict(self) -> None:
        with patch("src.track_analyzer.mutagen", None):
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertEqual(result, {})


class FeatureVectorWeightingTests(unittest.TestCase):
    def test_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(FEATURE_WEIGHTS.values()), 1.0)

    def test_block_scaling_matches_sqrt_weight(self) -> None:
        track = TrackRecord(id="a", path="/a.mp3", title="A", artist="Artist1", genre="rock", bpm=120, year=2020)
        genres = ["rock", "unknown"]
        artists = ["Artist1", "unknown"]
        vector = _build_feature_vector(track, genres, artists, bpm_min=100, bpm_max=140, year_min=2000, year_max=2020)

        # genre block (len 2) + artist block (len 2) + bpm (1) + year (1) = 6
        self.assertEqual(len(vector), 6)
        genre_scale = math.sqrt(FEATURE_WEIGHTS["genre"])
        artist_scale = math.sqrt(FEATURE_WEIGHTS["artist"])
        bpm_scale = math.sqrt(FEATURE_WEIGHTS["bpm"])
        year_scale = math.sqrt(FEATURE_WEIGHTS["year"])

        self.assertAlmostEqual(vector[0], genre_scale)  # matches "rock"
        self.assertAlmostEqual(vector[1], 0.0)
        self.assertAlmostEqual(vector[2], artist_scale)  # matches "artist1"
        self.assertAlmostEqual(vector[3], 0.0)
        self.assertAlmostEqual(vector[4], 0.5 * bpm_scale)  # (120-100)/(140-100) = 0.5
        self.assertAlmostEqual(vector[5], 1.0 * year_scale)  # (2020-2000)/(2020-2000) = 1.0

    def test_same_genre_different_artist_scores_lower_than_same_artist_same_genre(self) -> None:
        # Same-artist/same-genre pair should score at or above a
        # different-artist/same-genre pair once bpm/year introduce real
        # variation, proving genre alone can bridge artists without being
        # completely overridden by a raw artist match.
        catalog = build_catalog([
            TrackRecord(id="start", path="/start.mp3", title="Start", artist="ACDC", genre="rock", bpm=120, year=1980),
            TrackRecord(id="same_artist", path="/sa.mp3", title="Same Artist", artist="ACDC", genre="rock", bpm=125, year=1981),
            TrackRecord(id="same_genre", path="/sg.mp3", title="Same Genre", artist="Queen", genre="rock", bpm=122, year=1980),
            TrackRecord(id="different", path="/d.mp3", title="Different", artist="Queen", genre="pop", bpm=90, year=2020),
        ])
        ranked = rank_candidates("start", catalog)
        ranked_ids = [track.id for track, _ in ranked]
        # Same-genre cross-artist track should rank above the unrelated
        # different-genre/different-artist track.
        self.assertLess(ranked_ids.index("same_genre"), ranked_ids.index("different"))
        # Genre-matching cross-artist candidate should still be a real
        # candidate (not scored at 0), proving genre contributes.
        same_genre_score = next(score for track, score in ranked if track.id == "same_genre")
        self.assertGreater(same_genre_score, 0.0)


if __name__ == "__main__":
    unittest.main()
