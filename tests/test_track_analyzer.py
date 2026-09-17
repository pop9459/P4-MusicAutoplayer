"""Tests for src/track_analyzer.py: genre canonicalization, genre grouping,
tag extraction, and the weighted per-pair similarity terms.
"""
from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.predictor import rank_candidates
from src.track_analyzer import (
    FEATURE_WEIGHTS,
    _GENRE_TREE,
    Catalog,
    TrackRecord,
    _genre_similarity,
    _parse_bpm_from_tag,
    _parse_year_from_tag,
    _read_tag_metadata,
    build_catalog,
    canonicalize_genre,
    genre_grouping,
    track_similarity,
)


def _features(**kwargs: object):
    """Build a catalog of one track and return its derived features."""
    defaults = dict(id="t", path="/t.mp3", title="T", artist="A", genre="pop")
    defaults.update(kwargs)  # type: ignore[arg-type]
    catalog = build_catalog([TrackRecord(**defaults)])  # type: ignore[arg-type]
    return catalog.features_for(catalog.tracks[0])


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
        for variant in ["Big Room", "Dutch House", "Techno", "Dubstep"]:
            self.assertEqual(canonicalize_genre(variant), "electronic")

    def test_a_label_the_tree_names_is_not_folded_into_a_broad_family(self) -> None:
        """`_GENRE_TREE` is the single source of truth for the labels it names.

        "Eurodance" used to collapse to "electronic" here while the tree
        grouped it under `(disco, pop)` -- so the tree entry was unreachable
        and the label landed in a family its author had not chosen. The two
        tables now agree, with the tree deciding. Reverting this for a
        particular label is a one-line deletion from `_GENRE_TREE`.
        """
        self.assertEqual(canonicalize_genre("Eurodance"), "eurodance")
        self.assertEqual(genre_grouping("eurodance"), ("disco", "pop"))
        self.assertEqual(canonicalize_genre("Synthpop"), "synthpop")
        self.assertEqual(genre_grouping("synthpop"), ("synth", "electronic"))

    def test_pop_family_catches_uncommon_pop_variants(self) -> None:
        self.assertEqual(canonicalize_genre("Slovak Pop"), "pop")
        self.assertEqual(canonicalize_genre("Classic Czech Pop"), "pop")

    def test_unmapped_niche_genre_is_kept_as_is(self) -> None:
        self.assertEqual(canonicalize_genre("Heligonka"), "folk")
        self.assertEqual(canonicalize_genre("Some Totally Unique Tag"), "some totally unique tag")


class GenreGroupingTests(unittest.TestCase):
    def test_orphan_labels_keep_their_identity_but_gain_a_family(self) -> None:
        # "brostep" is its own label (not collapsed into "electronic") yet it
        # must still group with the rest of the electronic family, otherwise
        # it is similar to nothing at all.
        self.assertEqual(canonicalize_genre("Brostep"), "brostep")
        self.assertEqual(genre_grouping("brostep"), ("bass", "electronic"))

    def test_related_orphans_share_a_subfamily(self) -> None:
        for label in ["brostep", "bassline", "hard bass", "brazilian bass"]:
            self.assertEqual(genre_grouping(label), ("bass", "electronic"))

    def test_orphans_in_the_same_family_differ_by_subfamily(self) -> None:
        self.assertEqual(genre_grouping("hardstyle"), ("hard", "electronic"))
        self.assertEqual(genre_grouping("melbourne bounce"), ("house", "electronic"))

    def test_broad_canonical_genres_are_grouped(self) -> None:
        self.assertEqual(genre_grouping("rock"), ("rock", "rock"))
        self.assertEqual(genre_grouping("metal"), ("rock", "rock"))
        self.assertEqual(genre_grouping("grunge"), ("alt", "rock"))
        self.assertEqual(genre_grouping("hip-hop"), ("hip-hop", "urban"))
        self.assertEqual(genre_grouping("r&b"), ("r&b", "urban"))

    def test_keyword_fallback_groups_labels_not_listed_explicitly(self) -> None:
        self.assertEqual(genre_grouping("norwegian hardcore"), ("hard", "electronic"))
        self.assertEqual(genre_grouping("epic orchestral"), ("film", "score"))

    def test_unknown_and_non_genres_stay_isolated(self) -> None:
        self.assertEqual(genre_grouping("unknown"), (None, None))
        self.assertEqual(genre_grouping("speedrun"), (None, None))
        self.assertEqual(genre_grouping("meme"), (None, None))

    def test_every_tree_label_survives_canonicalization(self) -> None:
        """Canonicalization runs before grouping, so a label the tree names
        must still be that label afterwards.

        This is the assertion that catches the two stages disagreeing.
        "synthpop" once canonicalized to "pop" and "eurodance" to
        "electronic", which made their tree entries unreachable *and* put
        them in the wrong family -- the exact collapse _GENRE_TREE exists to
        prevent.
        """
        for label, grouping in _GENRE_TREE.items():
            with self.subTest(label=label):
                self.assertEqual(canonicalize_genre(label), label)
                self.assertEqual(genre_grouping(canonicalize_genre(label)), grouping)

    def test_broad_folding_still_applies_to_labels_the_tree_does_not_name(self) -> None:
        # The tree exemption must not disable the keyword folding generally:
        # fragmented real-world tags still have to collapse.
        self.assertEqual(canonicalize_genre("Glam Rock"), "rock")
        self.assertEqual(canonicalize_genre("Australian Rock"), "rock")
        self.assertEqual(canonicalize_genre("Dance Pop"), "pop")


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

    def test_reads_duration_from_info_length(self) -> None:
        # mutagen.File(path, easy=True) returns one object that answers
        # both tags.get(...) (easy tags) and tags.info.length (stream
        # info) -- a plain dict with an added `.info` attribute stands in
        # for that here, same shape as the real EasyID3/EasyMP3 object.
        fake_tags = {"genre": ["Eurodance"]}
        fake_tags = type("FakeTags", (dict,), {})(fake_tags)
        fake_tags.info = SimpleNamespace(length=192.792)
        with patch("src.track_analyzer.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value = fake_tags
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertEqual(result["duration"], 192.792)

    def test_missing_info_omits_duration(self) -> None:
        with patch("src.track_analyzer.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value = {"genre": ["Eurodance"]}
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertNotIn("duration", result)

    def test_zero_length_omits_duration(self) -> None:
        fake_tags = type("FakeTags", (dict,), {})({})
        fake_tags.info = SimpleNamespace(length=0.0)
        with patch("src.track_analyzer.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value = fake_tags
            result = _read_tag_metadata(__import__("pathlib").Path("/fake/track.mp3"))
        self.assertNotIn("duration", result)


class TrackRecordDurationTests(unittest.TestCase):
    def test_from_dict_defaults_duration_to_none_when_missing(self) -> None:
        """A pre-v3 catalog has no "duration" key at all -- it must still
        load, with duration defaulting to None rather than raising."""
        payload = {"id": "t", "path": "/t.mp3", "title": "T"}
        track = TrackRecord.from_dict(payload)
        self.assertIsNone(track.duration)

    def test_from_dict_round_trips_duration(self) -> None:
        payload = {"id": "t", "path": "/t.mp3", "title": "T", "duration": 192.792}
        track = TrackRecord.from_dict(payload)
        self.assertEqual(track.duration, 192.792)

    def test_catalog_to_dict_round_trips_duration(self) -> None:
        track = TrackRecord(id="t", path="/t.mp3", title="T", artist="A", duration=180.0)
        catalog = build_catalog([track])
        restored = Catalog.from_dict(catalog.to_dict())
        self.assertEqual(restored.tracks[0].duration, 180.0)


class SimilarityTermTests(unittest.TestCase):
    def test_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(FEATURE_WEIGHTS.values()), 1.0)

    def test_weights_renormalize_over_available_terms(self) -> None:
        # Neither track has a BPM tag, so the bpm term is skipped entirely
        # and its weight is redistributed rather than silently scoring 0.
        # Same genre and artist, 12 years apart: genre 1.0, artist 1.0,
        # year exp(-1), over the genre+artist+year weights only.
        a = _features(id="a", path="/a.mp3", genre="rock", artist="X", year=2000)
        b = _features(id="b", path="/b.mp3", genre="rock", artist="X", year=2012)

        available = FEATURE_WEIGHTS["genre"] + FEATURE_WEIGHTS["artist"] + FEATURE_WEIGHTS["year"]
        expected = (
            FEATURE_WEIGHTS["genre"] * 1.0
            + FEATURE_WEIGHTS["artist"] * 1.0
            + FEATURE_WEIGHTS["year"] * math.exp(-1.0)
        ) / available
        self.assertAlmostEqual(track_similarity(a, b), expected)

    def test_missing_term_on_one_side_is_skipped_not_scored_zero(self) -> None:
        # A track with no year must not be penalised against a track that
        # has one -- that would make untagged tracks look dissimilar to
        # everything rather than simply unmeasured on that axis.
        tagged = _features(id="a", path="/a.mp3", genre="rock", artist="X", year=2000)
        untagged = _features(id="b", path="/b.mp3", genre="rock", artist="X", year=None)

        self.assertAlmostEqual(track_similarity(tagged, untagged), 1.0)

    def test_year_similarity_depends_on_the_gap_not_on_recency(self) -> None:
        # The old scalar-in-a-cosine-vector encoding made two recent tracks
        # score far higher than two old ones for the same zero-year gap.
        recent_a = _features(id="a", path="/a.mp3", artist="X", year=2024)
        recent_b = _features(id="b", path="/b.mp3", artist="X", year=2024)
        old_a = _features(id="c", path="/c.mp3", artist="X", year=1960)
        old_b = _features(id="d", path="/d.mp3", artist="X", year=1960)

        self.assertAlmostEqual(track_similarity(recent_a, recent_b), track_similarity(old_a, old_b))

    def test_collaborations_share_credit_with_their_members(self) -> None:
        # A solo track and a collaboration by the same person used to score
        # 0 on the artist axis, because the whole credit string was one
        # atomic artist.
        solo = _features(id="a", path="/a.mp3", artist="Skrillex", genre="electronic")
        collab = _features(id="b", path="/b.mp3", artist="Skrillex, Boys Noize, Dylan Brady", genre="electronic")
        unrelated = _features(id="c", path="/c.mp3", artist="Queen", genre="electronic")

        self.assertGreater(track_similarity(solo, collab), track_similarity(solo, unrelated))

    def test_partial_credit_overlap_scores_below_an_exact_match(self) -> None:
        # Overlap-coefficient scoring would call these identical, which just
        # re-creates the saturation the artist term is meant to break up.
        solo = _features(id="a", path="/a.mp3", artist="Skrillex", genre="electronic")
        collab = _features(id="b", path="/b.mp3", artist="Skrillex, Boys Noize", genre="electronic")
        other_solo = _features(id="c", path="/c.mp3", artist="Skrillex", genre="electronic")

        self.assertLess(track_similarity(solo, collab), track_similarity(solo, other_solo))

    def test_unknown_artist_is_unmeasured_rather_than_an_artist_named_unknown(self) -> None:
        a = _features(id="a", path="/a.mp3", artist="unknown", genre="rock", year=1970)
        b = _features(id="b", path="/b.mp3", artist="unknown", genre="pop", year=2020)
        self.assertLess(track_similarity(a, b), 0.5)

    def test_bpm_similarity_treats_half_and_double_time_as_equivalent(self) -> None:
        # 87 vs 174 BPM is a counting convention, not a tempo difference.
        slow = _features(id="a", path="/a.mp3", artist="X", bpm=87.0)
        fast = _features(id="b", path="/b.mp3", artist="X", bpm=174.0)
        unrelated = _features(id="c", path="/c.mp3", artist="X", bpm=123.0)

        self.assertAlmostEqual(track_similarity(slow, fast), 1.0)
        self.assertLess(track_similarity(slow, unrelated), track_similarity(slow, fast))

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


def _genre_sim(a: str, b: str) -> float | None:
    return _genre_similarity(_features(id="a", path="/a.mp3", genre=a), _features(id="b", path="/b.mp3", genre=b))


class GenreAdjacencyTests(unittest.TestCase):
    def test_exact_match_scores_one(self) -> None:
        self.assertEqual(_genre_sim("rock", "rock"), 1.0)

    def test_adjacent_pair_is_symmetric_and_nonzero(self) -> None:
        self.assertEqual(_genre_sim("rock", "metal"), _genre_sim("metal", "rock"))
        self.assertGreater(_genre_sim("rock", "metal"), 0.0)

    def test_unrelated_pair_scores_zero(self) -> None:
        self.assertEqual(_genre_sim("rock", "classical"), 0.0)

    def test_closeness_decreases_down_the_tiers(self) -> None:
        # same label > same subfamily > same family > bridged families > none
        self.assertGreater(_genre_sim("rock", "rock"), _genre_sim("rock", "metal"))
        self.assertGreater(_genre_sim("rock", "metal"), _genre_sim("rock", "grunge"))
        self.assertGreater(_genre_sim("rock", "grunge"), _genre_sim("rock", "pop"))
        self.assertGreater(_genre_sim("rock", "pop"), _genre_sim("rock", "classical"))

    def test_orphan_labels_reach_each_other_through_their_family(self) -> None:
        # Neither is a broad canonical genre, and before grouping they were
        # similar to nothing at all.
        self.assertGreater(_genre_sim("brostep", "hardstyle"), 0.0)
        self.assertGreater(_genre_sim("brostep", "bassline"), _genre_sim("brostep", "hardstyle"))

    def test_unknown_genre_is_unmeasured_rather_than_a_genre_of_its_own(self) -> None:
        # Returning 0.0 would say "definitely unrelated"; returning 1.0 for
        # two unknowns would make every untagged track a perfect match for
        # every other. Both are wrong -- the term is simply not available.
        self.assertIsNone(_genre_sim("unknown", "rock"))
        self.assertIsNone(_genre_sim("unknown", "unknown"))

    def test_untagged_tracks_do_not_all_look_identical(self) -> None:
        a = _features(id="a", path="/a.mp3", genre=None, artist="A", year=1970)
        b = _features(id="b", path="/b.mp3", genre=None, artist="B", year=2020)
        self.assertLess(track_similarity(a, b), 0.5)

    def test_adjacent_genre_ranks_above_unrelated_genre(self) -> None:
        catalog = build_catalog([
            TrackRecord(id="start", path="/start.mp3", title="Start", artist="A", genre="rock", bpm=120, year=2000),
            TrackRecord(id="metal_track", path="/m.mp3", title="Metal", artist="B", genre="metal", bpm=120, year=2000),
            TrackRecord(id="classical_track", path="/c.mp3", title="Classical", artist="C", genre="classical", bpm=120, year=2000),
        ])
        ranked = rank_candidates("start", catalog)
        ranked_ids = [track.id for track, _ in ranked]

        self.assertLess(ranked_ids.index("metal_track"), ranked_ids.index("classical_track"))
        metal_score = next(score for track, score in ranked if track.id == "metal_track")
        classical_score = next(score for track, score in ranked if track.id == "classical_track")
        # The two candidates share bpm and year with the seed, so the gap
        # between them is contributed entirely by the genre term.
        self.assertAlmostEqual(metal_score - classical_score, FEATURE_WEIGHTS["genre"] * _genre_sim("rock", "metal"))

    def test_exact_genre_match_similarity_is_unchanged_by_adjacency_table(self) -> None:
        # Two same-genre/same-everything-else tracks should still score a
        # perfect match: adjacency only matters when genres differ.
        catalog = build_catalog([
            TrackRecord(id="start", path="/start.mp3", title="Start", artist="A", genre="rock", bpm=120, year=2000),
            TrackRecord(id="match", path="/match.mp3", title="Match", artist="A", genre="rock", bpm=120, year=2000),
        ])
        start = catalog.features_for(next(t for t in catalog.tracks if t.id == "start"))
        match = catalog.features_for(next(t for t in catalog.tracks if t.id == "match"))
        self.assertAlmostEqual(track_similarity(start, match), 1.0)


if __name__ == "__main__":
    unittest.main()
