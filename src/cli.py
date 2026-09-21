from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

from . import player_ui_v2
from .bpm_analyzer import BpmAnalyzerUnavailableError, analyze_tracks, require_aubio, tracks_needing_bpm
from .library import (
    Library,
    add_folder,
    library_needs_migration,
    list_folders,
    load_library,
    migrate_settings_to_library,
    new_library,
    remove_folder,
    rescan_folder,
    save_library,
)
from .predictor import find_track, generate_queue, rank_candidates, recommend_next_track
from .settings import DEFAULT_SETTINGS_PATH, Settings, load_settings
from .track_analyzer import (
    Catalog,
    TrackRecord,
    build_catalog,
    genre_grouping,
    load_catalog,
    save_catalog,
    scan_library,
)


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
    grouped_labels = sum(1 for genre in catalog.genres if genre_grouping(genre) != (None, None))
    ungrouped_tracks = sum(1 for track in catalog.tracks if genre_grouping(track.genre) == (None, None))
    print(f"Catalog version: {catalog.version}")
    print(f"Tracks: {len(catalog.tracks)} ({enabled_count} enabled, {len(catalog.tracks) - enabled_count} disabled)")
    print(f"Genres: {len(catalog.genres)} ({grouped_labels} grouped into a family)")
    print(f"Tracks with an ungrouped genre: {ungrouped_tracks}")
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
    track = find_track(catalog, args.track_id)
    print(_format_track(track))
    print(f"Path: {track.path}")
    print(f"Album: {track.album or 'unknown'}")
    features = catalog.features_for(track)
    subfamily, family = features.subfamily, features.family
    print(f"Genre: {track.genre} (family: {family or 'none'} / {subfamily or 'none'})")
    print(f"BPM: {track.bpm if track.bpm is not None else 'unknown'}")
    print(f"Year: {track.year if track.year is not None else 'unknown'}")
    print(f"Artist keys: {', '.join(sorted(features.artist_keys))}")


def _command_recommend(args: argparse.Namespace) -> None:
    catalog = _load_or_build_catalog(args.catalog, args.music_dir)
    current_track = find_track(catalog, args.track_id)
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
    current_track = find_track(catalog, args.track_id)
    queue = generate_queue(
        args.track_id,
        catalog,
        length=args.length,
        top_k=args.top_k,
        randomness=args.randomness,
        max_consecutive_same_artist=args.max_consecutive_artist,
    )

    print(f"Starting track: {_format_track(current_track)}")
    print(f"Generated {len(queue)} of {args.length} requested queue tracks.")
    for index, track in enumerate(queue, start=1):
        print(f"{index}. {_format_track(track)}")


def _command_play(args: argparse.Namespace, settings: Settings) -> None:
    settings_path = args.settings or DEFAULT_SETTINGS_PATH
    # Explicit flags override settings for this run only -- never written back.
    # _apply_settings_defaults has already filled each of these from settings
    # when the flag was absent, so this is a no-op unless one was passed.
    settings = replace(
        settings,
        top_k=args.top_k,
        randomness=args.randomness,
        queue_length=args.length,
    )
    if library_needs_migration(settings):
        library = migrate_settings_to_library(settings, settings_path)
    else:
        library = load_library(settings.library_path)
    exit_code = player_ui_v2.run(library, settings, settings_path)
    if exit_code:
        raise SystemExit(exit_code)


def _resolve_library_path(args: argparse.Namespace, settings: Settings | None) -> Path:
    if getattr(args, "library", None) is not None:
        return args.library
    if settings is not None:
        return settings.library_path
    raise ValueError("No library path given: pass --library or ensure settings.json exists.")


def _open_library(
    args: argparse.Namespace, settings: Settings | None, *, create_missing: bool = False
) -> tuple[Library, Path]:
    """Resolve the library path and load it. Every library command starts here."""
    library_path = _resolve_library_path(args, settings)
    if create_missing and not library_path.exists():
        return new_library(), library_path
    return load_library(library_path), library_path


def _progress_printer(label: str) -> Callable[[int, int], None]:
    """A `progress_callback` that redraws "label: scanned/total" in place."""

    def _print(scanned: int, total: int) -> None:
        print(f"\r{label}: {scanned}/{total}", end="", flush=True)

    return _print


def _command_add_folder(args: argparse.Namespace, settings: Settings | None = None) -> None:
    library, library_path = _open_library(args, settings, create_missing=True)
    new_lib, folder, was_added = add_folder(
        library, args.path, progress_callback=_progress_printer("Scanning")
    )
    print()
    save_library(new_lib, library_path)
    verb = "Added" if was_added else "Rescanned"
    print(f"{verb} folder {folder.path} ({folder.track_count} tracks). Library saved to {library_path}.")


def _command_list_folders(args: argparse.Namespace, settings: Settings | None = None) -> None:
    library, _ = _open_library(args, settings)
    for folder in list_folders(library):
        print(f"{folder.id}  {folder.track_count:5d} tracks  {folder.path}")
    print(f"{len(library.folders)} folders, {len(library.catalog.tracks)} tracks total.")


def _command_analyze_bpm(args: argparse.Namespace, settings: Settings | None = None) -> None:
    library, library_path = _open_library(args, settings)
    pending = tracks_needing_bpm(library.catalog.tracks)
    already = len(library.catalog.tracks) - len(pending)

    if not pending:
        print(f"All {len(library.catalog.tracks)} tracks already have a BPM. Nothing to do.")
        return

    try:
        require_aubio()
    except BpmAnalyzerUnavailableError as error:
        raise SystemExit(str(error)) from error

    print(f"{len(pending)} tracks need a BPM ({already} already done). Roughly {len(pending) * 0.4 / 60:.0f} min.")
    print("Safe to interrupt: progress is saved as it goes and a re-run picks up where it left off.")

    def _save() -> None:
        # Drop the analyzed tracks' cached features so they re-derive with the
        # new tempo on the next lookup. This used to call build_catalog over
        # the whole library on every checkpoint, which reconstructs every
        # TrackRecord and re-runs genre canonicalization for all of them --
        # the same result at a fraction of the cost. Same approach the TUI
        # already takes (see player_ui_v2._apply_bpm_results).
        for track in pending:
            library.catalog.track_features.pop(track.id, None)
        save_library(library, library_path)

    def _progress(index: int, total: int, track: TrackRecord) -> None:
        print(f"\r[{index}/{total}] {index * 100 // total}%  {track.title[:48]:<48}", end="", flush=True)

    try:
        analyzed, failed = analyze_tracks(pending, on_checkpoint=_save, on_progress=_progress)
    except KeyboardInterrupt:
        _save()
        print(f"\nInterrupted. Progress saved to {library_path}; re-run to continue.")
        return

    print(f"\nAnalyzed {analyzed} tracks. Library saved to {library_path}.")
    if failed:
        print(f"{len(failed)} tracks had no detectable tempo:")
        for track in failed[:10]:
            print(f"  {_format_track(track)}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")


def _command_remove_folder(args: argparse.Namespace, settings: Settings | None = None) -> None:
    library, library_path = _open_library(args, settings)
    new_lib = remove_folder(library, args.folder_id)
    save_library(new_lib, library_path)
    print(f"Removed folder {args.folder_id}. {len(new_lib.folders)} folders remain.")


def _command_rescan_folder(args: argparse.Namespace, settings: Settings | None = None) -> None:
    library, library_path = _open_library(args, settings)
    try:
        new_lib, track_count = rescan_folder(
            library, args.folder_id, progress_callback=_progress_printer("Rescanning")
        )
    except KeyError as error:
        raise SystemExit(str(error)) from error
    print()
    save_library(new_lib, library_path)
    print(f"Rescanned folder {args.folder_id} ({track_count} tracks). Library saved to {library_path}.")


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
    queue.add_argument("--max-consecutive-artist", type=_positive_int, help="Max same-artist tracks allowed in a row (omit for no cap). Defaults to settings.json.")
    queue.set_defaults(handler=_command_queue)

    play = subparsers.add_parser("play", help="Run the 3-column TUI player (requires mpv).")
    play.add_argument("--top-k", type=_positive_int, help="Number of highest-ranked candidates eligible for each selection. Defaults to settings.json.")
    play.add_argument("--randomness", type=_randomness, help="Weighted random selection factor from 0.0 to 1.0. Defaults to settings.json.")
    play.add_argument("--length", type=_positive_int, help="Queue length to (re)generate at a time. Defaults to settings.json.")
    play.set_defaults(handler=_command_play)

    add_folder_parser = subparsers.add_parser("add-folder", help="Recursively scan and track a music folder in the library.")
    add_folder_parser.add_argument("--path", type=Path, required=True, help="Folder to scan and add.")
    add_folder_parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    add_folder_parser.set_defaults(handler=_command_add_folder)

    list_folders_parser = subparsers.add_parser("list-folders", help="List tracked folders in the library.")
    list_folders_parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    list_folders_parser.set_defaults(handler=_command_list_folders)

    remove_folder_parser = subparsers.add_parser("remove-folder", help="Remove a tracked folder (and its tracks) from the library.")
    remove_folder_parser.add_argument("--folder-id", required=True, help="ID of the folder to remove (see list-folders).")
    remove_folder_parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    remove_folder_parser.set_defaults(handler=_command_remove_folder)

    rescan_folder_parser = subparsers.add_parser(
        "rescan-folder",
        help="Re-scan a tracked folder to refresh metadata (e.g. duration) for files still on disk.",
    )
    rescan_folder_parser.add_argument("--folder-id", required=True, help="ID of the folder to rescan (see list-folders).")
    rescan_folder_parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    rescan_folder_parser.set_defaults(handler=_command_rescan_folder)

    analyze_bpm_parser = subparsers.add_parser(
        "analyze-bpm",
        help="Detect tempo for library tracks with no BPM (needs the optional 'aubio' package).",
    )
    analyze_bpm_parser.add_argument("--library", type=Path, help="Path to library.json. Defaults to settings.json.")
    analyze_bpm_parser.set_defaults(handler=_command_analyze_bpm)

    return parser


def _apply_settings_defaults(args: argparse.Namespace, settings: Settings) -> None:
    if hasattr(args, "catalog") and args.catalog is None:
        args.catalog = settings.catalog_path
    if hasattr(args, "music_dir") and args.music_dir is None:
        args.music_dir = settings.music_directory
    if hasattr(args, "top_k") and args.top_k is None:
        args.top_k = settings.top_k
    if hasattr(args, "randomness") and args.randomness is None:
        args.randomness = settings.randomness
    if hasattr(args, "length") and args.length is None:
        args.length = settings.queue_length
    if hasattr(args, "max_consecutive_artist") and args.max_consecutive_artist is None:
        args.max_consecutive_artist = settings.max_consecutive_same_artist


_LIBRARY_COMMANDS = {"add-folder", "list-folders", "remove-folder", "rescan-folder", "analyze-bpm"}


def _needs_settings(args: argparse.Namespace) -> bool:
    if args.command == "play":
        return True
    if args.command in _LIBRARY_COMMANDS:
        return getattr(args, "library", None) is None
    if args.catalog is None:
        return True
    if not args.catalog.exists() and args.music_dir is None:
        return True
    if args.command == "build-catalog":
        return args.music_dir is None
    if args.command in {"recommend", "queue"} and (args.top_k is None or args.randomness is None):
        return True
    return args.command == "queue" and args.length is None


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = None
        if _needs_settings(args):
            settings = load_settings(args.settings or DEFAULT_SETTINGS_PATH)
            _apply_settings_defaults(args, settings)
        handler: Callable = args.handler
        if args.command == "play" or args.command in _LIBRARY_COMMANDS:
            handler(args, settings)
        else:
            handler(args)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
