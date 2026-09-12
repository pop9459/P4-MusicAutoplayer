from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SETTINGS_VERSION = 1
DEFAULT_SETTINGS_PATH = Path("settings.json")
DEFAULT_MAX_CONSECUTIVE_SAME_ARTIST = 3


@dataclass(frozen=True, slots=True)
class Settings:
    catalog_path: Path
    music_directory: Path
    music_folders: tuple[Path, ...] | None
    top_k: int
    randomness: float
    queue_length: int
    max_consecutive_same_artist: int | None

    def with_music_directory(self, new_directory: Path) -> Settings:
        """Return a new Settings with updated music_directory and folders."""
        from dataclasses import replace
        return replace(self, music_directory=new_directory, music_folders=(new_directory,))


def _require_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Settings field '{name}' must be a non-empty string")
    return value


def _require_positive_int(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Settings field '{name}' must be an integer of at least 1")
    return value


def _require_randomness(payload: dict[str, Any]) -> float:
    value = payload.get("randomness")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError("Settings field 'randomness' must be a number from 0.0 to 1.0")
    return float(value)


def _load_max_consecutive_same_artist(payload: dict[str, Any]) -> int | None:
    """Optional cap on same-artist tracks in a row; absent/null disables it.

    New field with a default, so existing settings.json files without it
    keep working (unlike top_k/randomness/queue_length, which are required).
    """
    if "max_consecutive_same_artist" not in payload:
        return DEFAULT_MAX_CONSECUTIVE_SAME_ARTIST
    value = payload.get("max_consecutive_same_artist")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Settings field 'max_consecutive_same_artist' must be a positive integer or null")
    return value


def _migrate_music_directory_to_folders(music_directory: str, settings_path: Path) -> tuple[Path, ...]:
    """Convert single music_directory to music_folders array for backward compatibility."""
    return (_resolve_path(music_directory, settings_path),)


def _load_music_folders(payload: dict[str, Any], settings_path: Path) -> tuple[Path, ...]:
    """Load music_folders if present; otherwise migrate from music_directory."""
    if "music_folders" in payload:
        folders = payload.get("music_folders")
        if not isinstance(folders, list) or not folders:
            raise ValueError("Settings field 'music_folders' must be a non-empty list of strings")
        resolved = tuple(_resolve_path(folder, settings_path) for folder in folders if isinstance(folder, str))
        if not resolved:
            raise ValueError("Settings field 'music_folders' must contain valid folder paths")
        return resolved
    # Backward compatibility: migrate music_directory to folders
    music_dir = _require_string(payload, "music_directory")
    return _migrate_music_directory_to_folders(music_dir, settings_path)


def _resolve_path(value: str, settings_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return settings_path.parent / path


def load_settings(path: str | Path = DEFAULT_SETTINGS_PATH) -> Settings:
    settings_path = Path(path)
    try:
        with settings_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise FileNotFoundError(f"Settings file not found: {settings_path}") from None
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in settings file {settings_path}: {error.msg}") from error

    if not isinstance(payload, dict):
        raise ValueError("Settings file must contain a JSON object")
    if payload.get("version") != SETTINGS_VERSION:
        raise ValueError(f"Settings field 'version' must be {SETTINGS_VERSION}")

    music_folders = _load_music_folders(payload, settings_path)
    music_directory = music_folders[0]

    return Settings(
        catalog_path=_resolve_path(_require_string(payload, "catalog_path"), settings_path),
        music_directory=music_directory,
        music_folders=music_folders,
        top_k=_require_positive_int(payload, "top_k"),
        randomness=_require_randomness(payload),
        queue_length=_require_positive_int(payload, "queue_length"),
        max_consecutive_same_artist=_load_max_consecutive_same_artist(payload),
    )


def save_settings(settings: Settings, path: str | Path = DEFAULT_SETTINGS_PATH) -> None:
    """Save settings back to JSON file, preserving music_folders if present."""
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve paths relative to settings file for storage
    def _relative_path(p: Path) -> str:
        try:
            return str(p.relative_to(settings_path.parent))
        except ValueError:
            return str(p)

    payload = {
        "version": SETTINGS_VERSION,
        "catalog_path": _relative_path(settings.catalog_path),
        "music_directory": _relative_path(settings.music_directory),
        "top_k": settings.top_k,
        "randomness": settings.randomness,
        "queue_length": settings.queue_length,
        "max_consecutive_same_artist": settings.max_consecutive_same_artist,
    }

    if settings.music_folders and len(settings.music_folders) > 1:
        payload["music_folders"] = [_relative_path(folder) for folder in settings.music_folders]

    with settings_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
