from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from .predictor import generate_queue, rank_candidates, recommend_next_track
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


def _add_catalog_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", type=Path, default=Path("data/tracks.json"), help="Path to the JSON catalog (default: data/tracks.json).")
    parser.add_argument("--music-dir", type=Path, help="Music directory to scan if the catalog does not exist.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and debug the local music recommender.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-catalog", help="Scan a music directory and write a JSON catalog.")
    build.add_argument("--music-dir", type=Path, required=True, help="Music directory to scan.")
    build.add_argument("--catalog", type=Path, default=Path("data/tracks.json"), help="Output JSON catalog path.")
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
    recommend.add_argument("--top-k", type=_positive_int, default=5, help="Number of highest-ranked candidates eligible for selection (default: 5).")
    recommend.add_argument("--show-candidates", type=_positive_int, default=10, help="Number of ranked candidates to display (default: 10).")
    recommend.add_argument("--randomness", type=_randomness, default=0.0, help="Weighted random selection factor from 0.0 to 1.0 (default: 0.0).")
    recommend.set_defaults(handler=_command_recommend)

    queue = subparsers.add_parser("queue", help="Generate a no-repeat recommendation queue.")
    _add_catalog_arguments(queue)
    queue.add_argument("--track-id", required=True, help="Starting track ID.")
    queue.add_argument("--length", type=_positive_int, default=10, help="Number of next tracks to generate (default: 10).")
    queue.add_argument("--top-k", type=_positive_int, default=5, help="Number of highest-ranked candidates eligible for each selection (default: 5).")
    queue.add_argument("--randomness", type=_randomness, default=0.0, help="Weighted random selection factor from 0.0 to 1.0 (default: 0.0).")
    queue.set_defaults(handler=_command_queue)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        handler: Callable[[argparse.Namespace], None] = args.handler
        handler(args)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
