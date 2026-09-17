"""Blind A/B listening test: the only instrument here that can say "good".

`tools/recommender_report.py` measures the shape of a session and
`tools/eval_holdout.py` scores the metric against a held-out grouping, but
both are proxies. Whether a queue is *enjoyable* is a question only a listener
can answer, and an unblinded "does this feel better?" answers it badly --
knowing which arm is the new one is enough to decide you prefer it.

So: two configurations continue the same seed track, their excerpts are played
as unlabelled "Set 1" and "Set 2" in a random order, and the verdict is
recorded. Enough trials and the win rate comes with a p-value.

    python tools/ab_listen.py                      # current vs a shuffle control
    python tools/ab_listen.py --trials 12 --picks 4
    python tools/ab_listen.py --arm current --arm "loose,top_k=25,randomness=0.6"
    python tools/ab_listen.py --report             # aggregate the log, play nothing

START WITH THE SHUFFLE CONTROL (the default second arm). If the recommender
cannot beat random shuffle by ear, no amount of weight tuning matters; if it
wins clearly, that is the first real evidence it is good, and it calibrates how
large a difference this harness can detect at all.

The log under eval/ is an evaluation artifact, never a user model -- see
eval/README.md. Nothing under src/ may read it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.library import load_library  # noqa: E402
from src.mpv_backend import MpvBackend, MpvUnavailableError  # noqa: E402
from src.settings import DEFAULT_SETTINGS_PATH, load_settings  # noqa: E402
from src.track_analyzer import Catalog, TrackRecord, canonicalize_genre, genre_grouping  # noqa: E402
from tools._session import play_session  # noqa: E402
from tools.eval_holdout import feature_weights  # noqa: E402

LOG_SCHEMA = 1
DEFAULT_LOG_PATH = Path("eval/ab_listening_log.jsonl")

_STRATEGIES = ("engine", "shuffle_genre", "shuffle_all")
_WEIGHT_KEYS = {"w_genre": "genre", "w_bpm": "bpm", "w_artist": "artist", "w_year": "year"}


# ---------------------------------------------------------------- arm configs


@dataclass(frozen=True, slots=True)
class ArmConfig:
    """One side of the comparison: how this arm builds its continuation."""

    name: str
    strategy: str = "engine"
    top_k: int = 5
    randomness: float = 0.15
    queue_length: int = 50
    max_consecutive_same_artist: int | None = 3
    weights: tuple[tuple[str, float], ...] = ()

    def params(self) -> dict[str, object]:
        """The config as it is written into the log."""
        return {
            "strategy": self.strategy,
            "top_k": self.top_k,
            "randomness": self.randomness,
            "queue_length": self.queue_length,
            "max_consecutive_same_artist": self.max_consecutive_same_artist,
            "weights": dict(self.weights),
        }


class ArmSpecError(ValueError):
    """A malformed --arm spec. Fatal rather than defaulted.

    A typo'd key must not silently run the default config: the trial would be
    logged under the name the user asked for, and the log is the one artefact
    that has to stay trustworthy across sessions.
    """


def parse_arm_spec(spec: str, settings) -> ArmConfig:
    """Parse `name[,key=value...]` into an ArmConfig, defaulting from settings."""
    segments = [segment.strip() for segment in spec.split(",") if segment.strip()]
    if not segments:
        raise ArmSpecError("Empty --arm spec")

    name = ""
    if "=" not in segments[0]:
        name = segments.pop(0)

    values: dict[str, str] = {}
    for segment in segments:
        if "=" not in segment:
            raise ArmSpecError(f"Expected key=value in --arm spec, got {segment!r}")
        key, _, value = segment.partition("=")
        values[key.strip()] = value.strip()

    name = values.pop("name", name)
    if not name:
        raise ArmSpecError(f"--arm spec needs a name: {spec!r}")

    strategy = values.pop("strategy", "engine")
    if strategy not in _STRATEGIES:
        raise ArmSpecError(f"Unknown strategy {strategy!r}; expected one of {', '.join(_STRATEGIES)}")

    weights: dict[str, float] = {}
    for key in list(values):
        if key in _WEIGHT_KEYS:
            weights[_WEIGHT_KEYS[key]] = float(values.pop(key))

    def take_int(key: str, default: int) -> int:
        return int(values.pop(key)) if key in values else default

    top_k = take_int("top_k", settings.top_k)
    queue_length = take_int("queue_length", settings.queue_length)
    randomness = float(values.pop("randomness")) if "randomness" in values else settings.randomness

    cap: int | None = settings.max_consecutive_same_artist
    if "max_consecutive_same_artist" in values:
        raw = values.pop("max_consecutive_same_artist")
        cap = None if raw.lower() in {"none", "off"} else int(raw)

    if values:
        raise ArmSpecError(f"Unknown --arm key(s): {', '.join(sorted(values))}")

    return ArmConfig(
        name=name,
        strategy=strategy,
        top_k=top_k,
        randomness=randomness,
        queue_length=queue_length,
        max_consecutive_same_artist=cap,
        weights=tuple(sorted(weights.items())),
    )


def default_arms(settings) -> tuple[ArmConfig, ArmConfig]:
    """Current settings against a same-genre shuffle -- the control to run first."""
    return (
        parse_arm_spec("current", settings),
        parse_arm_spec("genre-shuffle,strategy=shuffle_genre", settings),
    )


# ------------------------------------------------------------- continuations


def _shuffle_pool(catalog: Catalog, seed: TrackRecord, arm: ArmConfig) -> list[TrackRecord]:
    """Candidate pool for a shuffle control arm, widening if it is too thin."""
    enabled = [track for track in catalog.tracks if track.enabled and track.id != seed.id]
    if arm.strategy == "shuffle_all":
        return enabled

    genre = canonicalize_genre(seed.genre)
    same_genre = [track for track in enabled if canonicalize_genre(track.genre) == genre]
    if len(same_genre) >= 4:
        return same_genre

    _, family = genre_grouping(genre)
    if family is not None:
        same_family = [track for track in enabled if genre_grouping(canonicalize_genre(track.genre))[1] == family]
        if len(same_family) >= 4:
            return same_family
    return enabled


def continuation(
    catalog: Catalog,
    seed: TrackRecord,
    arm: ArmConfig,
    picks: int,
    rng_seed: int,
    settings,
) -> list[TrackRecord]:
    """The arm's `picks`-track continuation of `seed`, excluding the seed itself.

    The `engine` strategy runs through `PlayerEngine.advance()` (via the shared
    `play_session` helper), so a trial exercises the queue top-up, the artist
    cap and the work-key cooldown exactly as playback does -- `generate_queue`
    alone would skip all three.
    """
    if arm.strategy == "engine":
        with feature_weights(dict(arm.weights)):
            played = play_session(
                catalog,
                seed,
                picks,
                rng_seed,
                settings,
                top_k=arm.top_k,
                randomness=arm.randomness,
                queue_length=arm.queue_length,
                max_consecutive_same_artist=arm.max_consecutive_same_artist,
            )
        return played[1:]

    rng = random.Random(rng_seed)
    pool = _shuffle_pool(catalog, seed, arm)
    rng.shuffle(pool)

    # Dedupe on work_key so the control is not handed an unfair loss by
    # playing the same song twice under two filenames.
    chosen: list[TrackRecord] = []
    seen = {catalog.features_for(seed).work_key}
    for track in pool:
        key = catalog.features_for(track).work_key
        if key in seen:
            continue
        seen.add(key)
        chosen.append(track)
        if len(chosen) == picks:
            break
    return chosen


# ------------------------------------------------------------------ blinding


def blind_order(rng: random.Random) -> tuple[str, str]:
    """Return the arm keys in presentation order: ("A","B") or ("B","A")."""
    sides = ["A", "B"]
    rng.shuffle(sides)
    return sides[0], sides[1]


def verdict_from_answer(answer: str, order: tuple[str, str]) -> str | None:
    """Map a keypress to an arm key, "tie", "skip", "quit" -- or None to reprompt."""
    cleaned = answer.strip().lower()
    if cleaned == "1":
        return order[0]
    if cleaned == "2":
        return order[1]
    if cleaned in {"=", "t", "tie"}:
        return "tie"
    if cleaned in {"s", "skip"}:
        return "skip"
    if cleaned in {"q", "quit"}:
        return "quit"
    return None


def excerpt_window(duration: float | None, start_fraction: float, seconds: float) -> tuple[float, float]:
    """(start_seconds, play_seconds) for an excerpt, clamped to the track.

    An unreadable duration falls back to playing from the start: better a
    weaker excerpt than a skipped trial. A track shorter than the excerpt
    plays whole, and otherwise the start is pulled back so the excerpt never
    runs off the end.
    """
    if duration is None or duration <= 0:
        return 0.0, seconds
    if duration <= seconds:
        return 0.0, duration
    start = min(duration * start_fraction, duration - seconds)
    return max(0.0, start), seconds


# ------------------------------------------------------------------ playback


class ExcerptPlayer(Protocol):
    def play_excerpt(self, path: str, start_fraction: float, seconds: float) -> None: ...
    def stop(self) -> None: ...


class MpvExcerptPlayer:
    """Plays a fixed-length excerpt from partway into a track."""

    def __init__(self, backend: MpvBackend, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._backend = backend
        self._sleep = sleep
        self._monotonic = monotonic

    def play_excerpt(self, path: str, start_fraction: float, seconds: float) -> None:
        self._backend.load_file(path, paused=True)
        duration = self._backend.wait_until_playable(sleep=self._sleep)
        start, span = excerpt_window(duration, start_fraction, seconds)
        if start > 0.0:
            self._backend.seek(start)
        self._backend.resume()
        self._wait(span)
        self._backend.stop()

    def _wait(self, span: float) -> None:
        """Sleep in slices so Ctrl-C lands promptly and an early end is noticed."""
        deadline = self._monotonic() + span
        while self._monotonic() < deadline:
            if self._backend.is_finished():
                return
            self._sleep(min(0.2, max(0.0, deadline - self._monotonic())))

    def stop(self) -> None:
        self._backend.stop()


class NullExcerptPlayer:
    """Records excerpt requests instead of playing them. Tests and --no-audio."""

    def __init__(self) -> None:
        self.played: list[tuple[str, float, float]] = []

    def play_excerpt(self, path: str, start_fraction: float, seconds: float) -> None:
        self.played.append((path, start_fraction, seconds))

    def stop(self) -> None:
        return None


# ----------------------------------------------------------------------- log


def append_trial(log_path: Path, record: dict) -> None:
    """Append one trial and force it to disk before the next one starts."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_trials(log_path: Path) -> tuple[list[dict], int]:
    """Read the log, returning (records, lines skipped).

    A line from an unknown schema or a half-written final line from a kill is
    skipped rather than fatal -- one bad line must not cost every recorded
    verdict before it.
    """
    if not log_path.exists():
        return [], 0
    records: list[dict] = []
    skipped = 0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(record, dict) or record.get("schema") != LOG_SCHEMA:
            skipped += 1
            continue
        records.append(record)
    return records, skipped


# ----------------------------------------------------------------- reporting


def binomial_two_sided_p(successes: int, trials: int) -> float:
    """Exact two-sided binomial p-value against p=0.5, stdlib only.

    Under p=0.5 the distribution is symmetric about n/2, so the outcomes at
    least as extreme as the observed one are exactly the two mirrored tails
    and the p-value is twice the smaller. Summed as exact integers and divided
    once, so nothing is lost to rounding until the end.
    """
    if trials <= 0:
        return 1.0
    smaller = min(successes, trials - successes)
    tail = sum(math.comb(trials, i) for i in range(smaller + 1))
    return min(1.0, (2 * tail) / (2**trials))


@dataclass(frozen=True, slots=True)
class Tally:
    arms: tuple[str, str]
    recorded: int
    wins: tuple[tuple[str, int], ...]
    ties: int
    skipped: int

    @property
    def decisive(self) -> int:
        return sum(count for _, count in self.wins)

    def win_count(self, arm: str) -> int:
        return dict(self.wins).get(arm, 0)

    def win_rate(self, arm: str) -> float:
        return self.win_count(arm) / self.decisive if self.decisive else 0.0

    def p_value(self) -> float:
        return binomial_two_sided_p(self.win_count(self.arms[0]), self.decisive)


def _record_names(record: dict) -> dict[str, str]:
    return {key: arm["name"] for key, arm in record.get("arms", {}).items()}


def group_by_matchup(records: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    """Group trials by the sorted pair of config *names*.

    Names, not slot letters: "A" means a different config in every session, so
    grouping on the letter would sum unrelated comparisons into one total.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        names = _record_names(record)
        if len(names) != 2:
            continue
        key = tuple(sorted(names.values()))
        grouped.setdefault(key, []).append(record)  # type: ignore[arg-type]
    return grouped


def tally_matchup(records: Sequence[dict], arms: tuple[str, str]) -> Tally:
    """Count wins per config name. Ties and skips are excluded from `decisive`."""
    wins = {arms[0]: 0, arms[1]: 0}
    ties = skipped = 0
    for record in records:
        verdict = record.get("verdict")
        if verdict == "tie":
            ties += 1
            continue
        if verdict in {"skip", "quit", None}:
            skipped += 1
            continue
        name = _record_names(record).get(str(verdict))
        if name in wins:
            wins[name] += 1
        else:
            skipped += 1
    return Tally(
        arms=arms,
        recorded=len(records),
        wins=tuple(sorted(wins.items())),
        ties=ties,
        skipped=skipped,
    )


def format_report(tallies: Sequence[Tally], skipped_lines: int = 0) -> str:
    lines: list[str] = []
    for tally in tallies:
        lines.append(f"Matchup: {tally.arms[0]} vs {tally.arms[1]}")
        lines.append(
            f"  trials recorded              {tally.recorded} "
            f"({tally.decisive} decisive, {tally.ties} ties, {tally.skipped} skipped)"
        )
        for arm in tally.arms:
            share = f"{tally.win_rate(arm) * 100:.0f}%" if tally.decisive else "--"
            lines.append(f"  {arm:<28} {tally.win_count(arm)} wins ({share})")
        p_value = tally.p_value()
        lines.append(f"  two-sided binomial p         {p_value:.3f}")
        if tally.decisive == 0:
            verdict = "no decisive trials yet"
        elif p_value < 0.05:
            leader = max(tally.arms, key=tally.win_count)
            verdict = f"{leader} wins, significant at p<0.05"
        else:
            verdict = "not significant at p<0.05 -- more trials needed"
        lines.append(f"  verdict                      {verdict}")
        lines.append("")
    if skipped_lines:
        lines.append(f"({skipped_lines} unreadable log line(s) skipped)")
    return "\n".join(lines).rstrip("\n")


def report_log(log_path: Path, say: Callable[[str], None] = print) -> int:
    records, skipped = load_trials(log_path)
    if not records:
        say(f"No trials recorded in {log_path}.")
        return 1
    grouped = group_by_matchup(records)
    tallies = [tally_matchup(rows, arms) for arms, rows in sorted(grouped.items())]
    say(format_report(tallies, skipped))
    return 0


# --------------------------------------------------------------- trial loop


@dataclass
class _TrialContext:
    catalog: Catalog
    arms: tuple[ArmConfig, ArmConfig]
    settings: object
    picks: int
    excerpt_seconds: float
    excerpt_start: float
    reveal_titles: bool
    log_path: Path
    session_id: str
    rng_seed: int
    library_path: str
    recorded: list[dict] = field(default_factory=list)


def _describe(track: TrackRecord, reveal: bool) -> str:
    return f"{track.artist} - {track.title}" if reveal else "(hidden)"


def run_trials(
    context: _TrialContext,
    seeds: Sequence[TrackRecord],
    rng: random.Random,
    player: ExcerptPlayer,
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> list[dict]:
    """Run one blind trial per seed, appending each verdict as it is given."""
    by_key = {"A": context.arms[0], "B": context.arms[1]}

    for index, seed in enumerate(seeds, start=1):
        try:
            continuations = {
                key: continuation(context.catalog, seed, arm, context.picks, context.rng_seed + index, context.settings)
                for key, arm in by_key.items()
            }
            short = [key for key, tracks in continuations.items() if len(tracks) < context.picks]
            if short:
                # Unequal set lengths would themselves reveal which arm is
                # which, so the trial is dropped rather than presented.
                say(f"Trial {index}: skipped, an arm could only offer {min(len(t) for t in continuations.values())} tracks.")
                continue

            first, second = blind_order(rng)
            say("")
            say(f"--- Trial {index} of {len(seeds)} ---")
            say(f"Seed: {seed.artist} - {seed.title}")
            player.play_excerpt(seed.path, context.excerpt_start, context.excerpt_seconds)

            for slot, key in enumerate((first, second), start=1):
                ask(f"Press Enter to hear Set {slot} ")
                for position, track in enumerate(continuations[key], start=1):
                    say(f"  Set {slot} - {position}/{context.picks}  {_describe(track, context.reveal_titles)}")
                    player.play_excerpt(track.path, context.excerpt_start, context.excerpt_seconds)

            verdict = None
            while verdict is None:
                verdict = verdict_from_answer(
                    ask("Which set followed the seed better? [1/2/= tie/s skip/q quit] "), (first, second)
                )

            record = {
                "schema": LOG_SCHEMA,
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "session_id": context.session_id,
                "trial": index,
                "rng_seed": context.rng_seed,
                "seed_track": {"id": seed.id, "artist": seed.artist, "title": seed.title},
                "arms": {
                    key: {"name": arm.name, "params": arm.params(),
                          "track_ids": [t.id for t in continuations[key]]}
                    for key, arm in by_key.items()
                },
                "presented": [first, second],
                "verdict": verdict,
                "excerpt": {
                    "start_fraction": context.excerpt_start,
                    "seconds": context.excerpt_seconds,
                    "picks": context.picks,
                },
                "library_path": context.library_path,
            }
            append_trial(context.log_path, record)
            context.recorded.append(record)

            if verdict == "quit":
                say("Stopping at your request.")
                break
        except (KeyboardInterrupt, EOFError):
            say("")
            say(f"Interrupted -- {len(context.recorded)} trial(s) recorded.")
            break

    try:
        player.stop()
    except (KeyboardInterrupt, MpvUnavailableError):
        pass
    return context.recorded


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH, help="Settings JSON path.")
    parser.add_argument("--arm", action="append", default=None, help="Arm spec: name[,key=value...]. Give it twice.")
    parser.add_argument("--trials", type=int, default=8, help="How many seed tracks to run.")
    parser.add_argument("--picks", type=int, default=4, help="Tracks per continuation, per arm.")
    parser.add_argument("--excerpt", type=float, default=25.0, help="Excerpt length in seconds.")
    parser.add_argument("--excerpt-start", type=float, default=0.35, help="Where in the track the excerpt begins.")
    parser.add_argument("--seed-track", action="append", default=None, help="Pin a seed track id (repeatable).")
    parser.add_argument("--seed", type=int, help="RNG seed. Omitted means unpredictable, which keeps the blinding.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH, help="Trial log path.")
    parser.add_argument("--reveal-titles", action="store_true", help="Show track names during trials (breaks blinding).")
    parser.add_argument("--report", action="store_true", help="Aggregate the log and exit; plays nothing.")
    parser.add_argument("--no-audio", action="store_true", help="Run without mpv. Smoke test only; invalidates verdicts.")
    args = parser.parse_args(argv)

    if args.report:
        return report_log(args.log)

    settings = load_settings(args.settings)
    library_path = args.library or settings.library_path
    library = load_library(library_path)
    catalog = library.catalog
    tracks = [track for track in catalog.tracks if track.enabled]
    if not tracks:
        print(f"No enabled tracks in {library_path}.")
        return 1

    try:
        specs = args.arm or []
        if len(specs) > 2:
            raise ArmSpecError("Give at most two --arm specs")
        if len(specs) == 1:
            raise ArmSpecError("Give two --arm specs, or none for the default matchup")
        arms = tuple(parse_arm_spec(spec, settings) for spec in specs) if specs else default_arms(settings)
    except (ArmSpecError, ValueError) as error:
        print(f"Bad --arm spec: {error}")
        return 2
    if arms[0].name == arms[1].name:
        print("The two arms need different names, or the log cannot tell them apart.")
        return 2

    # The seed also decides which arm is presented first, so a fixed default
    # would let anyone who reran the tool learn the mapping. Unpredictable
    # during the session, reconstructible afterwards from the log.
    rng_seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**32)
    rng = random.Random(rng_seed)

    if args.seed_track:
        by_id = {track.id: track for track in tracks}
        missing = [track_id for track_id in args.seed_track if track_id not in by_id]
        if missing:
            print(f"Seed track(s) not in the library: {', '.join(missing)}")
            return 1
        seeds = [by_id[track_id] for track_id in args.seed_track]
    else:
        seeds = rng.sample(tracks, min(args.trials, len(tracks)))

    context = _TrialContext(
        catalog=catalog,
        arms=arms,
        settings=settings,
        picks=args.picks,
        excerpt_seconds=args.excerpt,
        excerpt_start=args.excerpt_start,
        reveal_titles=args.reveal_titles,
        log_path=args.log,
        session_id=uuid.uuid4().hex[:8],
        rng_seed=rng_seed,
        library_path=str(library_path),
    )

    print(f"Library: {library_path}")
    print(f"{len(seeds)} trial(s), {args.picks} tracks per set, {args.excerpt:.0f}s excerpts.")
    print("The two sets are unlabelled and their order is randomised per trial.")
    print()

    if args.no_audio:
        print("--no-audio: nothing will be played. This invalidates any verdict you give.")
        run_trials(context, seeds, rng, NullExcerptPlayer())
    else:
        try:
            with MpvBackend() as backend:
                run_trials(context, seeds, rng, MpvExcerptPlayer(backend))
        except MpvUnavailableError as error:
            print(f"Cannot play audio: {error}")
            return 1

    print()
    print(f"RNG seed for this session: {rng_seed}")
    print()
    report_log(args.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
