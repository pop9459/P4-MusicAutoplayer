from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac"}
CATALOG_VERSION = 1


def _normalize_text(value: str | None, default: str = "") -> str:
    if not value:
        return default
    return " ".join(value.strip().split())


def canonicalize_genre(value: str | None) -> str:
    cleaned = _normalize_text(value, default="unknown").lower()
    if not cleaned:
        return "unknown"
    cleaned = re.sub(r"[\s_]+", " ", cleaned)
    alias_map = {
        "dance pop": "pop",
        "electro pop": "pop",
        "edm": "electronic",
        "hip hop": "hip-hop",
        "hiphop": "hip-hop",
        "r and b": "r&b",
        "rnb": "r&b",
    }
    return alias_map.get(cleaned, cleaned)


def _canonicalize_artist(value: str | None) -> str:
    cleaned = _normalize_text(value, default="unknown")
    return cleaned or "unknown"


def _track_id_from_path(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:16]


def _split_artist_title(stem: str) -> tuple[str, str]:
    parts = stem.split(" - ", 1)
    if len(parts) == 2:
        artist, title = parts
        return _normalize_text(artist, "unknown"), _normalize_text(title, stem)
    return "unknown", _normalize_text(stem, stem)


@dataclass(slots=True)
class TrackRecord:
    id: str
    path: str
    title: str
    artist: str = "unknown"
    album: str = ""
    genre: str = "unknown"
    bpm: float | None = None
    year: int | None = None
    enabled: bool = True
    feature_vector: list[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrackRecord":
        feature_vector = payload.get("feature_vector") or []
        return cls(
            id=str(payload["id"]),
            path=str(payload["path"]),
            title=str(payload["title"]),
            artist=_canonicalize_artist(payload.get("artist")),
            album=_normalize_text(payload.get("album"), ""),
            genre=canonicalize_genre(payload.get("genre")),
            bpm=_coerce_float(payload.get("bpm")),
            year=_coerce_int(payload.get("year")),
            enabled=bool(payload.get("enabled", True)),
            feature_vector=[float(value) for value in feature_vector],
        )


@dataclass(slots=True)
class Catalog:
    version: int
    tracks: list[TrackRecord]
    genres: list[str]
    artists: list[str]
    bpm_min: float | None
    bpm_max: float | None
    year_min: int | None
    year_max: int | None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Catalog":
        tracks = [TrackRecord.from_dict(item) for item in payload.get("tracks", [])]
        feature_space = payload.get("feature_space", {})
        return cls(
            version=int(payload.get("version", CATALOG_VERSION)),
            tracks=tracks,
            genres=[str(value) for value in feature_space.get("genres", [])],
            artists=[str(value) for value in feature_space.get("artists", [])],
            bpm_min=_coerce_float(feature_space.get("bpm_min")),
            bpm_max=_coerce_float(feature_space.get("bpm_max")),
            year_min=_coerce_int(feature_space.get("year_min")),
            year_max=_coerce_int(feature_space.get("year_max")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "feature_space": {
                "genres": self.genres,
                "artists": self.artists,
                "bpm_min": self.bpm_min,
                "bpm_max": self.bpm_max,
                "year_min": self.year_min,
                "year_max": self.year_max,
            },
            "tracks": [asdict(track) for track in self.tracks],
        }


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_numeric(value: float | int | None, minimum: float | int | None, maximum: float | int | None) -> float:
    if value is None or minimum is None or maximum is None:
        return 0.0
    if maximum == minimum:
        return 0.0
    return (float(value) - float(minimum)) / (float(maximum) - float(minimum))


def scan_library(root: str | Path) -> list[TrackRecord]:
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Music library not found: {root_path}")

    tracks: list[TrackRecord] = []
    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue
        artist, title = _split_artist_title(path.stem)
        tracks.append(
            TrackRecord(
                id=_track_id_from_path(path),
                path=str(path.resolve()),
                title=title,
                artist=artist,
                album="",
                genre="unknown",
                bpm=None,
                year=None,
                enabled=True,
            )
        )
    return tracks


def _build_feature_space(tracks: Iterable[TrackRecord]) -> tuple[list[str], list[str], float | None, float | None, int | None, int | None]:
    genres = sorted({canonicalize_genre(track.genre) for track in tracks} | {"unknown"})
    artists = sorted({_canonicalize_artist(track.artist) for track in tracks} | {"unknown"})

    bpm_values = [track.bpm for track in tracks if track.bpm is not None]
    year_values = [track.year for track in tracks if track.year is not None]

    bpm_min = min(bpm_values) if bpm_values else None
    bpm_max = max(bpm_values) if bpm_values else None
    year_min = min(year_values) if year_values else None
    year_max = max(year_values) if year_values else None

    return genres, artists, bpm_min, bpm_max, year_min, year_max


def _build_feature_vector(
    track: TrackRecord,
    genres: list[str],
    artists: list[str],
    bpm_min: float | None,
    bpm_max: float | None,
    year_min: int | None,
    year_max: int | None,
) -> list[float]:
    genre = canonicalize_genre(track.genre)
    artist = _canonicalize_artist(track.artist)
    genre_vector = [1.0 if genre == value else 0.0 for value in genres]
    artist_vector = [1.0 if artist == value else 0.0 for value in artists]
    numeric_vector = [
        _normalize_numeric(track.bpm, bpm_min, bpm_max),
        _normalize_numeric(track.year, year_min, year_max),
    ]
    return genre_vector + artist_vector + numeric_vector


def build_catalog(tracks: Iterable[TrackRecord]) -> Catalog:
    track_list = list(tracks)
    genres, artists, bpm_min, bpm_max, year_min, year_max = _build_feature_space(track_list)

    enriched_tracks: list[TrackRecord] = []
    for track in track_list:
        enriched_tracks.append(
            TrackRecord(
                id=track.id,
                path=track.path,
                title=_normalize_text(track.title, track.title),
                artist=_canonicalize_artist(track.artist),
                album=_normalize_text(track.album, ""),
                genre=canonicalize_genre(track.genre),
                bpm=track.bpm,
                year=track.year,
                enabled=track.enabled,
                feature_vector=_build_feature_vector(track, genres, artists, bpm_min, bpm_max, year_min, year_max),
            )
        )

    return Catalog(
        version=CATALOG_VERSION,
        tracks=enriched_tracks,
        genres=genres,
        artists=artists,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
        year_min=year_min,
        year_max=year_max,
    )


def load_catalog(path: str | Path) -> Catalog:
    catalog_path = Path(path)
    with catalog_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return Catalog.from_dict(payload)


def save_catalog(catalog: Catalog, path: str | Path) -> None:
    catalog_path = Path(path)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    with catalog_path.open("w", encoding="utf-8") as handle:
        json.dump(catalog.to_dict(), handle, indent=2, ensure_ascii=True)
        handle.write("\n")