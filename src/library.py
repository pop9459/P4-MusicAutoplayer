from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .json_io import load_json, save_json
from .settings import Settings
from .track_analyzer import (
    Catalog,
    TrackRecord,
    _id_from_path,
    build_catalog,
    load_catalog,
    scan_library,
    scan_library_with_progress,
)

LIBRARY_VERSION = 2
ALL_TRACKS_FOLDER_ID = "__all__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _folder_id_from_path(path: Path) -> str:
    return _id_from_path(path)


@dataclass(slots=True)
class LibraryFolder:
    id: str
    path: str
    display_name: str
    added_at: str
    last_scanned_at: str
    track_count: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LibraryFolder":
        return cls(
            id=str(payload["id"]),
            path=str(payload["path"]),
            display_name=str(payload.get("display_name") or Path(payload["path"]).name),
            added_at=str(payload.get("added_at", "")),
            last_scanned_at=str(payload.get("last_scanned_at", "")),
            track_count=int(payload.get("track_count", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "display_name": self.display_name,
            "added_at": self.added_at,
            "last_scanned_at": self.last_scanned_at,
            "track_count": self.track_count,
        }


@dataclass(slots=True)
class Library:
    version: int
    folders: list[LibraryFolder]
    catalog: Catalog

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Library":
        folders = [LibraryFolder.from_dict(item) for item in payload.get("folders", [])]
        catalog = Catalog.from_dict(payload.get("catalog", {}))
        return cls(
            version=int(payload.get("version", LIBRARY_VERSION)),
            folders=folders,
            catalog=catalog,
        )

    def to_dict(self) -> dict[str, Any]:
        # Always stamp the current version: what gets written is always the
        # current shape, so carrying a loaded v1 file's number through would
        # label an already-migrated file as v1.
        return {
            "version": LIBRARY_VERSION,
            "folders": [folder.to_dict() for folder in self.folders],
            "catalog": self.catalog.to_dict(),
        }


def new_library() -> Library:
    return Library(version=LIBRARY_VERSION, folders=[], catalog=build_catalog([]))


def load_library(path: str | Path) -> Library:
    return Library.from_dict(load_json(path))


def save_library(library: Library, path: str | Path) -> None:
    save_json(path, library.to_dict())


def find_folder_by_path(library: Library, path: str | Path) -> LibraryFolder | None:
    resolved = str(Path(path).resolve())
    for folder in library.folders:
        if folder.path == resolved:
            return folder
    return None


def add_folder(
    library: Library,
    path: str | Path,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[Library, LibraryFolder, bool]:
    """Add a folder to the library: scan only this folder, merge into the
    existing catalog, and rebuild the global feature space over all tracks.

    Returns (library, folder, was_added). If the resolved path already
    matches a tracked folder, this rescans it instead (see rescan_folder) --
    refreshing metadata such as duration for files still on disk -- and
    returns was_added=False so callers can distinguish "added a new folder"
    from "refreshed an existing one" for their own messaging, even though
    both branches now mutate and return a real library.
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Folder not found: {resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"Not a directory: {resolved}")

    existing = find_folder_by_path(library, resolved)
    if existing is not None:
        new_library_value, _ = rescan_folder(library, existing.id, progress_callback=progress_callback)
        updated_folder = next(f for f in new_library_value.folders if f.id == existing.id)
        return new_library_value, updated_folder, False

    folder_id = _folder_id_from_path(resolved)
    new_tracks = scan_library_with_progress(
        resolved, folder_id=folder_id, progress_callback=progress_callback
    )
    merged_tracks = list(library.catalog.tracks) + new_tracks
    new_catalog = build_catalog(merged_tracks)

    timestamp = _now_iso()
    folder = LibraryFolder(
        id=folder_id,
        path=str(resolved),
        display_name=resolved.name,
        added_at=timestamp,
        last_scanned_at=timestamp,
        track_count=len(new_tracks),
    )
    new_library_value = Library(
        version=LIBRARY_VERSION,
        folders=[*library.folders, folder],
        catalog=new_catalog,
    )
    return new_library_value, folder, True


def rescan_folder(
    library: Library,
    folder_id: str,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[Library, int]:
    """Re-scan an already-tracked folder, refreshing metadata for files still
    on disk (e.g. duration for tracks scanned before that field existed).

    Drops tracks for files no longer in the folder and picks up new ones,
    same as a fresh add_folder scan would. Each surviving track's `enabled`
    flag is carried over from the existing record, since a fresh scan
    otherwise defaults to enabled=True and would silently re-enable tracks
    the user disabled.
    """
    folder = next((f for f in library.folders if f.id == folder_id), None)
    if folder is None:
        raise KeyError(f"Unknown folder id: {folder_id}")

    resolved = Path(folder.path)
    if not resolved.exists():
        raise FileNotFoundError(f"Folder no longer exists: {resolved}")

    fresh_tracks = scan_library_with_progress(
        resolved, folder_id=folder_id, progress_callback=progress_callback
    )

    previously_enabled = {
        track.id: track.enabled
        for track in library.catalog.tracks
        if track.folder_id == folder_id
    }
    for track in fresh_tracks:
        if track.id in previously_enabled:
            track.enabled = previously_enabled[track.id]

    other_tracks = [track for track in library.catalog.tracks if track.folder_id != folder_id]
    new_catalog = build_catalog(other_tracks + fresh_tracks)

    updated_folder = LibraryFolder(
        id=folder.id,
        path=folder.path,
        display_name=folder.display_name,
        added_at=folder.added_at,
        last_scanned_at=_now_iso(),
        track_count=len(fresh_tracks),
    )
    new_folders = [updated_folder if f.id == folder_id else f for f in library.folders]
    new_library_value = Library(version=LIBRARY_VERSION, folders=new_folders, catalog=new_catalog)
    return new_library_value, len(fresh_tracks)


def remove_folder(library: Library, folder_id: str) -> Library:
    remaining_folders = [folder for folder in library.folders if folder.id != folder_id]
    remaining_tracks = [track for track in library.catalog.tracks if track.folder_id != folder_id]
    return Library(
        version=LIBRARY_VERSION,
        folders=remaining_folders,
        catalog=build_catalog(remaining_tracks),
    )


def list_folders(library: Library) -> list[LibraryFolder]:
    return list(library.folders)


def tracks_for_folder(library: Library, folder_id: str) -> list[TrackRecord]:
    if folder_id == ALL_TRACKS_FOLDER_ID:
        return list(library.catalog.tracks)
    return [track for track in library.catalog.tracks if track.folder_id == folder_id]


def library_needs_migration(settings: Settings) -> bool:
    return not settings.library_path.exists()


def migrate_settings_to_library(settings: Settings, settings_path: Path) -> Library:
    """One-time, non-destructive migration for pre-library.json settings.

    Loads the old catalog if it exists (preserving any manually curated
    `enabled` flags) rather than rescanning from disk; otherwise scans each
    legacy music_folders entry fresh. Tracks are stamped with a folder_id by
    matching their resolved path against each legacy folder (longest-prefix
    match handles nested legacy folders deterministically). The old catalog
    file, if any, is left on disk untouched — never deleted or moved.
    """
    legacy_folders = list(settings.music_folders or ())

    if settings.catalog_path is not None and settings.catalog_path.exists():
        old_catalog = load_catalog(settings.catalog_path)
        all_tracks = list(old_catalog.tracks)
    else:
        all_tracks = []
        for folder in legacy_folders:
            all_tracks.extend(scan_library(folder))

    folders: list[LibraryFolder] = []
    resolved_legacy = [(folder, str(folder.resolve())) for folder in legacy_folders]
    # Longest path first so a nested folder wins over its parent when
    # matching a track's resolved path to a legacy folder.
    resolved_legacy.sort(key=lambda pair: len(pair[1]), reverse=True)

    stamped_tracks: list[TrackRecord] = []
    for track in all_tracks:
        folder_id = ""
        for _, resolved_path in resolved_legacy:
            if track.path == resolved_path or track.path.startswith(resolved_path + "/"):
                folder_id = _folder_id_from_path(Path(resolved_path))
                break
        stamped_tracks.append(
            TrackRecord(
                id=track.id,
                path=track.path,
                title=track.title,
                artist=track.artist,
                album=track.album,
                genre=track.genre,
                bpm=track.bpm,
                year=track.year,
                enabled=track.enabled,
                folder_id=folder_id,
                duration=track.duration,
            )
        )

    timestamp = _now_iso()
    for folder, resolved_path in resolved_legacy:
        folder_id = _folder_id_from_path(Path(resolved_path))
        track_count = sum(1 for track in stamped_tracks if track.folder_id == folder_id)
        folders.append(
            LibraryFolder(
                id=folder_id,
                path=resolved_path,
                display_name=Path(resolved_path).name,
                added_at=timestamp,
                last_scanned_at=timestamp,
                track_count=track_count,
            )
        )
    # Restore original settings.music_folders order for a stable display.
    folders.sort(key=lambda f: [str(p.resolve()) for p in legacy_folders].index(f.path))

    catalog = build_catalog(stamped_tracks)
    library = Library(version=LIBRARY_VERSION, folders=folders, catalog=catalog)
    save_library(library, settings.library_path)
    return library


@dataclass
class ScanTask:
    """Wraps a background thread running add_folder(), polled from the TUI's
    render loop. The thread never touches curses/mpv state — it only
    produces a (Library, LibraryFolder, bool) result or an exception."""

    thread: threading.Thread | None
    lock: threading.Lock = field(default_factory=threading.Lock)
    done: threading.Event = field(default_factory=threading.Event)
    _progress: list[int] = field(default_factory=lambda: [0, 0])
    result: list[Any] = field(default_factory=list)
    error: list[BaseException] = field(default_factory=list)

    def progress(self) -> tuple[int, int]:
        with self.lock:
            return self._progress[0], self._progress[1]


def start_add_folder_task(library: Library, path: str | Path) -> ScanTask:
    task = ScanTask(thread=None)

    def _on_progress(scanned: int, total: int) -> None:
        with task.lock:
            task._progress[0] = scanned
            task._progress[1] = total

    def _run() -> None:
        try:
            outcome = add_folder(library, path, progress_callback=_on_progress)
            task.result.append(outcome)
        except (FileNotFoundError, NotADirectoryError, OSError) as error:
            task.error.append(error)
        finally:
            task.done.set()

    thread = threading.Thread(target=_run, daemon=True)
    task.thread = thread
    thread.start()
    return task
