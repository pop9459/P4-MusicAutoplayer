"""Tests for tools/ab_listen.py.

The harness is mostly pure functions plus one I/O shell, so almost everything
is testable without mpv or a terminal. The assertions that matter most are the
blinding ones -- a leak there invalidates every verdict the tool has ever
recorded, silently.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.settings import Settings
from src.track_analyzer import TrackRecord, build_catalog
from tools.ab_listen import (
    ArmConfig,
    ArmSpecError,
    MpvExcerptPlayer,
    NullExcerptPlayer,
    _TrialContext,
    append_trial,
    binomial_two_sided_p,
    blind_order,
    continuation,
    default_arms,
    excerpt_window,
    format_report,
    group_by_matchup,
    load_trials,
    parse_arm_spec,
    run_trials,
    tally_matchup,
    verdict_from_answer,
)

_SETTINGS = Settings(
    library_path=Path("data/library.json"),
    top_k=5,
    randomness=0.15,
    queue_length=50,
    max_consecutive_same_artist=2,
)


def _catalog(count: int = 30):
    genres = ["rock", "pop", "electronic"]
    return build_catalog(
        [
            TrackRecord(
                id=f"t{i}",
                path=f"/music/t{i}.mp3",
                title=f"Track {i}",
                artist=f"Artist {i % 9}",
                album=f"Album {i % 11}",
                genre=genres[i % len(genres)],
                bpm=90.0 + i,
                year=1990 + i,
                folder_id="f1",
            )
            for i in range(count)
        ]
    )


class BinomialTests(unittest.TestCase):
    def test_a_clean_sweep_is_significant(self) -> None:
        self.assertAlmostEqual(binomial_two_sided_p(10, 10), 2 / 1024)

    def test_an_even_split_is_maximally_unsurprising(self) -> None:
        self.assertEqual(binomial_two_sided_p(5, 10), 1.0)

    def test_eight_of_ten_matches_the_known_value(self) -> None:
        self.assertAlmostEqual(binomial_two_sided_p(8, 10), 0.109375)

    def test_no_trials_is_not_evidence(self) -> None:
        self.assertEqual(binomial_two_sided_p(0, 0), 1.0)

    def test_the_test_is_symmetric(self) -> None:
        for wins in range(13):
            self.assertAlmostEqual(binomial_two_sided_p(wins, 12), binomial_two_sided_p(12 - wins, 12))

    def test_a_p_value_never_exceeds_one(self) -> None:
        for trials in range(0, 15):
            for wins in range(trials + 1):
                self.assertLessEqual(binomial_two_sided_p(wins, trials), 1.0)


class ExcerptWindowTests(unittest.TestCase):
    def test_excerpt_starts_partway_into_a_long_track(self) -> None:
        self.assertEqual(excerpt_window(200.0, 0.35, 25.0), (70.0, 25.0))

    def test_a_short_track_plays_whole(self) -> None:
        self.assertEqual(excerpt_window(10.0, 0.35, 25.0), (0.0, 10.0))

    def test_the_start_is_pulled_back_so_the_excerpt_fits(self) -> None:
        start, span = excerpt_window(30.0, 0.9, 25.0)

        self.assertEqual(span, 25.0)
        self.assertEqual(start, 5.0)

    def test_an_unreadable_duration_falls_back_to_the_start(self) -> None:
        self.assertEqual(excerpt_window(None, 0.35, 25.0), (0.0, 25.0))


class BlindingTests(unittest.TestCase):
    def test_both_orderings_occur(self) -> None:
        rng = random.Random(0)
        seen = {blind_order(rng) for _ in range(50)}

        self.assertEqual(seen, {("A", "B"), ("B", "A")})

    def test_an_order_is_always_a_permutation_of_the_two_arms(self) -> None:
        rng = random.Random(3)
        for _ in range(20):
            self.assertEqual(set(blind_order(rng)), {"A", "B"})

    def test_a_keypress_maps_through_the_presented_order_not_the_arm_name(self) -> None:
        # This is the core of the blinding: "1" must mean whichever arm was
        # played first, which is B as often as it is A.
        self.assertEqual(verdict_from_answer("1", ("B", "A")), "B")
        self.assertEqual(verdict_from_answer("2", ("B", "A")), "A")
        self.assertEqual(verdict_from_answer("1", ("A", "B")), "A")

    def test_tie_skip_and_quit_are_recognised(self) -> None:
        order = ("A", "B")
        self.assertEqual(verdict_from_answer("=", order), "tie")
        self.assertEqual(verdict_from_answer("s", order), "skip")
        self.assertEqual(verdict_from_answer("Q", order), "quit")

    def test_junk_is_reprompted_rather_than_guessed(self) -> None:
        self.assertIsNone(verdict_from_answer("maybe", ("A", "B")))
        self.assertIsNone(verdict_from_answer("", ("A", "B")))


class ParseArmSpecTests(unittest.TestCase):
    def test_a_bare_name_takes_every_default_from_settings(self) -> None:
        arm = parse_arm_spec("current", _SETTINGS)

        self.assertEqual(arm.name, "current")
        self.assertEqual(arm.top_k, _SETTINGS.top_k)
        self.assertEqual(arm.randomness, _SETTINGS.randomness)
        self.assertEqual(arm.max_consecutive_same_artist, _SETTINGS.max_consecutive_same_artist)

    def test_overrides_are_applied(self) -> None:
        arm = parse_arm_spec("loose,top_k=25,randomness=0.6", _SETTINGS)

        self.assertEqual(arm.top_k, 25)
        self.assertEqual(arm.randomness, 0.6)

    def test_weight_overrides_are_collected(self) -> None:
        arm = parse_arm_spec("nobpm,w_bpm=0,w_genre=0.5", _SETTINGS)

        self.assertEqual(dict(arm.weights), {"bpm": 0.0, "genre": 0.5})

    def test_the_artist_cap_can_be_switched_off(self) -> None:
        self.assertIsNone(parse_arm_spec("uncapped,max_consecutive_same_artist=none", _SETTINGS).max_consecutive_same_artist)

    def test_an_unknown_key_is_fatal_rather_than_silently_defaulted(self) -> None:
        # A typo that ran the default config would be logged under the name
        # the user asked for, quietly poisoning the log.
        with self.assertRaises(ArmSpecError):
            parse_arm_spec("typo,top-k=25", _SETTINGS)

    def test_an_unknown_strategy_is_rejected(self) -> None:
        with self.assertRaises(ArmSpecError):
            parse_arm_spec("weird,strategy=telepathy", _SETTINGS)

    def test_a_nameless_spec_is_rejected(self) -> None:
        with self.assertRaises(ArmSpecError):
            parse_arm_spec("top_k=5", _SETTINGS)

    def test_the_default_matchup_pits_the_recommender_against_a_shuffle(self) -> None:
        first, second = default_arms(_SETTINGS)

        self.assertEqual(first.strategy, "engine")
        self.assertEqual(second.strategy, "shuffle_genre")


class ContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()
        self.seed = self.catalog.tracks[0]

    def test_the_engine_arm_returns_the_requested_number_of_distinct_tracks(self) -> None:
        arm = parse_arm_spec("current", _SETTINGS)

        tracks = continuation(self.catalog, self.seed, arm, 5, 1, _SETTINGS)

        self.assertEqual(len(tracks), 5)
        self.assertEqual(len({track.id for track in tracks}), 5)
        self.assertNotIn(self.seed.id, {track.id for track in tracks})

    def test_a_continuation_is_deterministic_for_a_fixed_rng_seed(self) -> None:
        arm = parse_arm_spec("current", _SETTINGS)

        first = continuation(self.catalog, self.seed, arm, 4, 7, _SETTINGS)
        second = continuation(self.catalog, self.seed, arm, 4, 7, _SETTINGS)

        self.assertEqual([t.id for t in first], [t.id for t in second])

    def test_the_genre_shuffle_control_stays_inside_the_seed_genre(self) -> None:
        arm = parse_arm_spec("control,strategy=shuffle_genre", _SETTINGS)

        tracks = continuation(self.catalog, self.seed, arm, 4, 1, _SETTINGS)

        self.assertEqual(len(tracks), 4)
        for track in tracks:
            self.assertEqual(track.genre, self.seed.genre)

    def test_the_shuffle_all_control_is_not_confined_to_one_genre(self) -> None:
        arm = parse_arm_spec("floor,strategy=shuffle_all", _SETTINGS)

        genres = {track.genre for track in continuation(self.catalog, self.seed, arm, 12, 1, _SETTINGS)}

        self.assertGreater(len(genres), 1)

    def test_weight_overrides_do_not_leak_out_of_the_arm(self) -> None:
        from src.track_analyzer import FEATURE_WEIGHTS

        original = dict(FEATURE_WEIGHTS)
        arm = parse_arm_spec("nobpm,w_bpm=0", _SETTINGS)

        continuation(self.catalog, self.seed, arm, 3, 1, _SETTINGS)

        self.assertEqual(FEATURE_WEIGHTS, original)


class TallyTests(unittest.TestCase):
    def _record(self, verdict: str, names: tuple[str, str] = ("current", "shuffle")) -> dict:
        return {
            "schema": 1,
            "arms": {
                "A": {"name": names[0], "params": {}, "track_ids": []},
                "B": {"name": names[1], "params": {}, "track_ids": []},
            },
            "verdict": verdict,
        }

    def test_wins_are_counted_against_config_names_not_slot_letters(self) -> None:
        # "A" is a different config in every session, so counting letters
        # would sum unrelated comparisons together.
        records = [
            self._record("A", ("current", "shuffle")),
            self._record("A", ("shuffle", "current")),
        ]

        tally = tally_matchup(records, ("current", "shuffle"))

        self.assertEqual(tally.win_count("current"), 1)
        self.assertEqual(tally.win_count("shuffle"), 1)

    def test_ties_and_skips_are_recorded_but_excluded_from_the_test(self) -> None:
        records = [self._record("A"), self._record("tie"), self._record("skip"), self._record("B")]

        tally = tally_matchup(records, ("current", "shuffle"))

        self.assertEqual(tally.recorded, 4)
        self.assertEqual(tally.decisive, 2)
        self.assertEqual(tally.ties, 1)
        self.assertEqual(tally.skipped, 1)

    def test_win_rate_is_a_share_of_decisive_trials(self) -> None:
        records = [self._record("A"), self._record("A"), self._record("B"), self._record("tie")]

        tally = tally_matchup(records, ("current", "shuffle"))

        self.assertAlmostEqual(tally.win_rate("current"), 2 / 3)

    def test_separate_matchups_do_not_contaminate_each_other(self) -> None:
        records = [
            self._record("A", ("current", "shuffle")),
            self._record("A", ("current", "loose")),
        ]

        grouped = group_by_matchup(records)

        self.assertEqual(len(grouped), 2)
        self.assertIn(("current", "shuffle"), grouped)
        self.assertIn(("current", "loose"), grouped)

    def test_a_report_names_a_significant_winner(self) -> None:
        tally = tally_matchup([self._record("A") for _ in range(10)], ("current", "shuffle"))

        report = format_report([tally])

        self.assertIn("current", report)
        self.assertIn("significant at p<0.05", report)

    def test_a_report_refuses_to_call_a_close_result(self) -> None:
        records = [self._record("A"), self._record("B")]
        tally = tally_matchup(records, ("current", "shuffle"))

        self.assertIn("not significant", format_report([tally]))


class LogTests(unittest.TestCase):
    def test_a_trial_round_trips(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "log.jsonl"
            append_trial(path, {"schema": 1, "verdict": "A"})

            records, skipped = load_trials(path)

        self.assertEqual(records, [{"schema": 1, "verdict": "A"}])
        self.assertEqual(skipped, 0)

    def test_a_missing_log_is_empty_rather_than_an_error(self) -> None:
        with TemporaryDirectory() as directory:
            self.assertEqual(load_trials(Path(directory) / "absent.jsonl"), ([], 0))

    def test_a_half_written_line_does_not_cost_the_trials_before_it(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "log.jsonl"
            append_trial(path, {"schema": 1, "verdict": "A"})
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"schema": 1, "verd')

            records, skipped = load_trials(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(skipped, 1)

    def test_a_line_from_an_unknown_schema_is_skipped(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "log.jsonl"
            append_trial(path, {"schema": 99, "verdict": "A"})

            records, skipped = load_trials(path)

        self.assertEqual(records, [])
        self.assertEqual(skipped, 1)


class ScriptedAsk:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class RunTrialsTests(unittest.TestCase):
    def _context(self, directory: str, **overrides) -> _TrialContext:
        catalog = _catalog()
        defaults = dict(
            catalog=catalog,
            arms=default_arms(_SETTINGS),
            settings=_SETTINGS,
            picks=2,
            excerpt_seconds=5.0,
            excerpt_start=0.35,
            reveal_titles=False,
            log_path=Path(directory) / "log.jsonl",
            session_id="test",
            rng_seed=11,
            library_path="/tmp/library.json",
        )
        return _TrialContext(**(defaults | overrides))

    def _seeds(self, context: _TrialContext, count: int):
        return context.catalog.tracks[:count]

    def test_each_trial_writes_exactly_one_line(self) -> None:
        with TemporaryDirectory() as directory:
            context = self._context(directory)
            ask = ScriptedAsk(["", "", "1"] * 3)

            recorded = run_trials(
                context, self._seeds(context, 3), random.Random(1), NullExcerptPlayer(),
                ask=ask, say=lambda _m: None,
            )
            lines = context.log_path.read_text().strip().splitlines()

        self.assertEqual(len(recorded), 3)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn(json.loads(line)["verdict"], {"A", "B"})

    def test_an_interrupt_keeps_every_completed_trial(self) -> None:
        class Interrupting(ScriptedAsk):
            def __call__(self, prompt: str) -> str:
                if len(self.prompts) >= 3:
                    raise KeyboardInterrupt
                return super().__call__(prompt)

        with TemporaryDirectory() as directory:
            context = self._context(directory)
            messages: list[str] = []

            run_trials(
                context, self._seeds(context, 4), random.Random(1), NullExcerptPlayer(),
                ask=Interrupting(["", "", "1", "", "", "2"]), say=messages.append,
            )
            lines = context.log_path.read_text().strip().splitlines()

        self.assertEqual(len(lines), 1)
        self.assertTrue(any("Interrupted" in message for message in messages))

    def test_both_arms_get_the_same_number_of_excerpts(self) -> None:
        # A set that is one track shorter announces which arm it is.
        with TemporaryDirectory() as directory:
            context = self._context(directory)
            player = NullExcerptPlayer()

            run_trials(
                context, self._seeds(context, 1), random.Random(1), player,
                ask=ScriptedAsk(["", "", "1"]), say=lambda _m: None,
            )

        # One seed excerpt plus picks-per-arm for two arms.
        self.assertEqual(len(player.played), 1 + 2 * context.picks)
        self.assertEqual({seconds for _, _, seconds in player.played}, {context.excerpt_seconds})

    def test_track_titles_stay_hidden_until_the_verdict_is_in(self) -> None:
        with TemporaryDirectory() as directory:
            context = self._context(directory)
            messages: list[str] = []
            seed = self._seeds(context, 1)[0]

            run_trials(
                context, [seed], random.Random(1), NullExcerptPlayer(),
                ask=ScriptedAsk(["", "", "1"]), say=messages.append,
            )

            transcript = "\n".join(messages)
            record = json.loads(context.log_path.read_text().strip())

        played_titles = {
            track.title
            for track in context.catalog.tracks
            if track.id in set(record["arms"]["A"]["track_ids"] + record["arms"]["B"]["track_ids"])
        }
        for title in played_titles - {seed.title}:
            self.assertNotIn(title, transcript)

    def test_the_log_records_which_arm_was_played_first(self) -> None:
        with TemporaryDirectory() as directory:
            context = self._context(directory)

            run_trials(
                context, self._seeds(context, 1), random.Random(1), NullExcerptPlayer(),
                ask=ScriptedAsk(["", "", "1"]), say=lambda _m: None,
            )
            record = json.loads(context.log_path.read_text().strip())

        self.assertEqual(set(record["presented"]), {"A", "B"})
        self.assertEqual(record["verdict"], record["presented"][0])

    def test_quitting_stops_the_run(self) -> None:
        with TemporaryDirectory() as directory:
            context = self._context(directory)

            recorded = run_trials(
                context, self._seeds(context, 5), random.Random(1), NullExcerptPlayer(),
                ask=ScriptedAsk(["", "", "q"]), say=lambda _m: None,
            )

        self.assertEqual(len(recorded), 1)


class MpvExcerptPlayerTests(unittest.TestCase):
    def _player(self, duration: float | None = 200.0):
        backend = MagicMock()
        backend.wait_until_playable.return_value = duration
        backend.is_finished.return_value = False
        clock = iter([0.0, 99.0, 99.0])
        return MpvExcerptPlayer(backend, sleep=lambda _s: None, monotonic=lambda: next(clock)), backend

    def test_an_excerpt_loads_paused_seeks_then_resumes(self) -> None:
        player, backend = self._player()

        player.play_excerpt("/music/t.mp3", 0.35, 25.0)

        backend.load_file.assert_called_once_with("/music/t.mp3", paused=True)
        backend.seek.assert_called_once_with(70.0)
        backend.resume.assert_called_once()
        backend.stop.assert_called_once()

    def test_an_unreadable_duration_plays_from_the_start_without_seeking(self) -> None:
        player, backend = self._player(duration=None)

        player.play_excerpt("/music/t.mp3", 0.35, 25.0)

        backend.seek.assert_not_called()
        backend.resume.assert_called_once()


class EvalLogBoundaryTests(unittest.TestCase):
    def test_nothing_under_src_reads_the_eval_log(self) -> None:
        """The privacy boundary, enforced rather than merely documented.

        CLAUDE.md keeps this product free of listening-history models. The A/B
        log is exactly that kind of data, so the recommender must never learn
        the path.
        """
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["grep", "-rn", "eval/", str(root / "src")],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout, "", "src/ must not reference the eval log directory")


if __name__ == "__main__":
    unittest.main()
