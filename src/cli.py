from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from . import player
from .predictor import generate_queue, rank_candidates, recommend_next_track
from .settings import DEFAULT_SETTINGS_PATH, Settings, load_settings
from .track_analyzer import Catalog, TrackRecord, build_catalog, load_catalog, save_catalog, scan_library


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _randomness(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0.0 and 1.0")
    return parsed


def _format_track(track: TrackRecord) -> str:
    status = "enabled" if track.enabled else "disabled"
    return f"{track.id}  [{status}]  {track.artist} - {track.title}"


def _find_track(catalog: Catalog, track_id: str) -> TrackRecord:
    for track in catalog.tracks:
        if track.id == track_id:
            return track
    raise ValueError(f"Track not found in catalog: {track_id}")


def _build_catalog(music_dir: Path, catalog_path: Path) -> Catalog:
    catalog = build_catalog(scan_library(music_dir))
    save_catalog(catalog, catalog_path)
    return catalog


def _load_or_build_catalog(catalog_path: Path, music_dir: Path | None) -> Catalog:
    if catalog_path.exists():
        return load_catalog(catalog_path)
    if music_dir is None:
        raise FileNotFoundError(
            f"Catalog not found: {catalog_path}. Run 'build-catalog' or provide --music-dir to create it."
        )
    return _build_catalog(music_dir, catalog_path)


def _print_summary(catalog: Catalog) -> None:
    enabled_count = sum(track.enabled for track in catalog.tracks)
    vector_size = len(catalog.tracks[0].feature_vector) if catalog.tracks else 0
    print(f"Catalog version: {catalog.version}")
    print(f"Tracks: {len(catalog.tracks)} ({enabled_count} enabled, {len(catalog.tracks) - enabled_count} disabled)")
    print(f"Feature vector size: {vector_size}")
    print(f"Genres: {len(catalog.genres)}")
    print(f"Artists: {len(catalog.artists)}")
    print(f"BPM range: {catalog.bpm_min if catalog.bpm_min is not None else 'unknown'} to {catalog.bpm_max if catalog.bpm_max is not None else 'unknown'}")
    print(f"Year range: {catalog.year_min if catalog.year_min is not None else 'unknown'} to {catalog.year_max if catalog.year_max is not None else 'unknown'}")


def _command_build_catalog(args: argparse.Namespace) -> None:
    catalog = _build_catalog(args.music_dir, args.catalog)
    print(f"Created {args.catalog} with {len(catalog.tracks)} tracks.")


def _command_summary(args: argparse.Namespace) -> None:
    _print_summary(_load_or_build_catalog(args.catalog, args.music_dir))


def _command_list_tracks(args: argparse.Namespace) -> None:
    catalog = _load_or_build_catalog(args.catalog, args.music_dir)
    tracks = catalog.tracks
    if args.enabled_only:
        tracks = [track for track in tracks if track.enabled]
    for track in tracks[: args.limit]:
        print(_format_track(track))
    print(f"Displayed {min(len(tracks), args.limit)} of {len(tracks)} tracks.")


def _command_inspect_track(args: argparse.Namespace) -> None:
    catalog = _load_or_build_catalog(args.catalog, args.music_dir)
    track = _find_track(catalog, args.track_id)
    print(_format_track(track))
    print(f"Path: {track.path}")
    print(f"Album: {track.album or 'unknown'}")
    print(f"Genre: {track.genre}")
    print(f"BPM: {track.bpm if track.bpm is not None else 'unknown'}")
    print(f"Year: {track.year if track.year is not None else 'unknown'}")
    print(f"Feature vector ({len(track.feature_vector)} values): {track.feature_vector}")


def _command_recommend(args: argparse.Namespace) -> None:
    catalog = _load_or_build_catalog(args.catalog, args.music_dir)
    current_track = _find_track(catalog, args.track_id)
    ranked_candidates = rank_candidates(args.track_id, catalog)
    top_candidates = ranked_candidates[: args.top_k]

    print(f"Current track: {_format_track(current_track)}")
    print(f"Eligible candidates analyzed: {len(ranked_candidates)}")
    print(f"Top-k selection pool: {len(top_candidates)} of {args.top_k}")
    print("Ranked candidates:")
    for index, (track, score) in enumerate(ranked_candidates[: args.show_candidates], start=1):
        print(f"{index}. {score:.6f}  {_format_track(track)}")

    recommendation = recommend_next_track(
        args.track_id,
        catalog,
        top_k=args.top_k,
        randomness=args.randomness,
    )
    print(f"Recommendation (randomness={args.randomness:.2f}): {_format_track(recommendation)}")


def _command_queue(args: argparse.Namespace) -> None:
    catalog = _load_or_build_catalog(args.catalog, args.music_dir)
    current_track = _find_track(catalog, args.track_id)
    queue = generate_queue(
        args.track_id,
        catalog,
        length=args.length,
        top_k=args.top_k,
        randomness=args.randomness,
    )

    print(f"Starting track: {_format_track(current_track)}")
    print(f"Generated {len(queue)} of {args.length} requested queue tracks.")
    for index, track in enumerate(queue, start=1):
        print(f"{index}. {_format_track(track)}")


def _command_play(args: argparse.Namespace) -> None:
    catalog = _load_or_build_catalog(args.catalog, args.music_dir)
    exit_code = player.run(
        catalog,
        top_k=args.top_k,
        randomness=args.randomness,
        queue_length=args.length,
        start_track_id=args.track_id,
    )
    if exit_code:
        raise SystemExit(exit_code)


def _add_catalog_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", type=Path, help="Path to the JSON catalog. Defaults to settings.json.")
    parser.add_argument("--music-dir", type=Path, help="Music directory to scan if the catalog does not exist. Defaults to settings.json.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and debug the local music recommender.")
    parser.add_argument("--settings", type=Path, help="Settings JSON path (default: settings.json when needed).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-catalog", help="Scan a music directory and write a JSON catalog.")
    build.add_argument("--music-dir", type=Path, help="Music directory to scan. Defaults to settings.json.")
    build.add_argument("--catalog", type=Path, help="Output JSON catalog path. Defaults to settings.json.")
    build.set_defaults(handler=_command_build_catalog)

    summary = subparsers.add_parser("summary", help="Show catalog and feature-space statistics.")
    _add_catalog_arguments(summary)
    summary.set_defaults(handler=_command_summary)

    list_tracks = subparsers.add_parser("list-tracks", help="List tracks and their IDs.")
    _add_catalog_arguments(list_tracks)
    list_tracks.add_argument("--limit", type=_positive_int, default=20, help="Maximum number of tracks to display (default: 20).")
    list_tracks.add_argument("--enabled-only", action="store_true", help="Display only eligible tracks.")
    list_tracks.set_defaults(handler=_command_list_tracks)

    inspect_track = subparsers.add_parser("inspect-track", help="Show metadata and features for one track.")
    _add_catalog_arguments(inspect_track)
    inspect_track.add_argument("--track-id", required=True, help="Track ID to inspect.")
    inspect_track.set_defaults(handler=_command_inspect_track)

    recommend = subparsers.add_parser("recommend", help="Show ranked candidates and a next-track recommendation.")
    _add_catalog_arguments(recommend)
    recommend.add_argument("--track-id", required=True, help="Current track ID.")
    recommend.add_argument("--top-k", type=_positive_int, help="Number of highest-ranked candidates eligible for selection. Defaults to settings.json.")
    recommend.add_argument("--show-candidates", type=_positive_int, default=10, help="Number of ranked candidates to display (default: 10).")
    recommend.add_argument("--randomness", type=_randomness, help="Weighted random selection factor from 0.0 to 1.0. Defaults to settings.json.")
    recommend.set_defaults(handler=_command_recommend)

    queue = subparsers.add_parser("queue", help="Generate a no-repeat recommendation queue.")
    _add_catalog_arguments(queue)
    queue.add_argument("--track-id", required=True, help="Starting track ID.")
    queue.add_argument("--length", type=_positive_int, help="Number of next tracks to generate. Defaults to settings.json.")
    queue.add_argument("--top-k", type=_positive_int, help="Number of highest-ranked candidates eligible for each selection. Defaults to settings.json.")
    queue.add_argument("--randomness", type=_randomness, help="Weighted random selection factor from 0.0 to 1.0. Defaults to settings.json.")
    queue.set_defaults(handler=_command_queue)

    play = subparsers.add_parser("play", help="Run the demo TUI player (requires mpv).")
    _add_catalog_arguments(play)
    play.add_argument("--track-id", help="Starting track ID. If omitted, an interactive picker is shown.")
    play.add_argument("--top-k", type=_positive_int, help="Number of highest-ranked candidates eligible for each selection. Defaults to settings.json.")
    play.add_argument("--randomness", type=_randomness, help="Weighted random selection factor from 0.0 to 1.0. Defaults to settings.json.")
    play.add_argument("--length", type=_positive_int, help="Queue length to (re)generate at a time. Defaults to settings.json.")
    play.set_defaults(handler=_command_play)

    return parser


def _apply_settings_defaults(args: argparse.Namespace, settings: Settings) -> None:
    if args.catalog is None:
        args.catalog = settings.catalog_path
    if args.music_dir is None:
        args.music_dir = settings.music_directory
    if hasattr(args, "top_k") and args.top_k is None:
        args.top_k = settings.top_k
    if hasattr(args, "randomness") and args.randomness is None:
        args.randomness = settings.randomness
    if hasattr(args, "length") and args.length is None:
        args.length = settings.queue_length


def _needs_settings(args: argparse.Namespace) -> bool:
    if args.catalog is None:
        return True
    if not args.catalog.exists() and args.music_dir is None:
        return True
    if args.command == "build-catalog":
        return args.music_dir is None
    if args.command in {"recommend", "queue", "play"} and (args.top_k is None or args.randomness is None):
        return True
    return args.command in {"queue", "play"} and args.length is None


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if _needs_settings(args):
            _apply_settings_defaults(args, load_settings(args.settings or DEFAULT_SETTINGS_PATH))
        handler: Callable[[argparse.Namespace], None] = args.handler
        handler(args)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
