from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SETTINGS_VERSION = 1
DEFAULT_SETTINGS_PATH = Path("settings.json")


@dataclass(frozen=True, slots=True)
class Settings:
    catalog_path: Path
    music_directory: Path
    top_k: int
    randomness: float
    queue_length: int


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

    return Settings(
        catalog_path=_resolve_path(_require_string(payload, "catalog_path"), settings_path),
        music_directory=_resolve_path(_require_string(payload, "music_directory"), settings_path),
        top_k=_require_positive_int(payload, "top_k"),
        randomness=_require_randomness(payload),
        queue_length=_require_positive_int(payload, "queue_length"),
    )
