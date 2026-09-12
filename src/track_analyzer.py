from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import mutagen
except ImportError:  # pragma: no cover - exercised only when dependency missing
    mutagen = None

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac"}
CATALOG_VERSION = 1
_YEAR_PATTERN = re.compile(r"(\d{4})")

# Relative influence of each feature block on recommendation similarity.
# Weights sum to 1.0. Each block's vector is scaled by sqrt(weight) before
# concatenation, so its contribution to a cosine-similarity dot product
# scales by exactly `weight`. Without this, one-hot genre/artist blocks
# implicitly dominate over small 0..1 numeric features (bpm/year), and a
# same-artist match alone can trivially produce near-maximal similarity.
FEATURE_WEIGHTS = {
    "genre": 0.35,
    "bpm": 0.25,
    "year": 0.20,
    "artist": 0.20,
}


def _scale_block(values: list[float], weight: float) -> list[float]:
    scale = math.sqrt(weight)
    return [value * scale for value in values]


def _normalize_text(value: str | None, default: str = "") -> str:
    if not value:
        return default
    return " ".join(value.strip().split())


def canonicalize_genre(value: str | None) -> str:
    cleaned = _normalize_text(value, default="unknown").lower()
    if not cleaned:
        return "unknown"
    cleaned = re.sub(r"[\s_]+", " ", cleaned)

    exact_alias_map = {
        "dance pop": "pop",
        "electro pop": "pop",
        "edm": "electronic",
        "hip hop": "hip-hop",
        "hiphop": "hip-hop",
        "r and b": "r&b",
        "rnb": "r&b",
    }
    if cleaned in exact_alias_map:
        return exact_alias_map[cleaned]

    # Real-world genre tags are highly fragmented (e.g. "australian rock",
    # "classic rock", "album rock" are all just "rock"). Without folding
    # these into broad families, genre similarity can't bridge related
    # artists tagged with slightly different strings, and content-based
    # recommendations end up isolated to a single artist's exact tag.
    # Checked in order from most to least specific so compound genres
    # (e.g. "hip hop soul") resolve predictably.
    keyword_families: list[tuple[str, tuple[str, ...]]] = [
        ("hip-hop", ("hip hop", "hiphop", "rap", "trap")),
        ("r&b", ("r&b", "r and b", "rnb", "soul")),
        ("electronic", ("house", "edm", "electro", "techno", "trance", "dubstep", "dance", "big room")),
        ("metal", ("metal",)),
        ("rock", ("rock",)),
        ("country", ("country",)),
        ("reggae", ("reggae", "dancehall")),
        ("latin", ("latin", "reggaeton", "salsa", "cumbia")),
        ("folk", ("folk", "ludov", "heligonka")),
        ("jazz", ("jazz",)),
        ("classical", ("classical",)),
        ("pop", ("pop",)),
    ]
    for canonical, keywords in keyword_families:
        if any(keyword in cleaned for keyword in keywords):
            return canonical

    return cleaned


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


def _parse_year_from_tag(value: str | None) -> int | None:
    """Extract a 4-digit year from a tag value like '2014-04-04' or '2014'."""
    if not value:
        return None
    match = _YEAR_PATTERN.search(value)
    if not match:
        return None
    return int(match.group(1))


def _parse_bpm_from_tag(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _read_tag_metadata(path: Path) -> dict[str, Any]:
    """Read embedded genre/year/album/bpm tags, tolerating missing/corrupt data.

    Returns a dict with keys 'genre', 'year', 'album', 'bpm' where each value
    is present only if a usable tag was found; callers should fall back to
    filename-derived defaults for any missing key.
    """
    result: dict[str, Any] = {}
    if mutagen is None:
        return result

    try:
        tags = mutagen.File(path, easy=True)
    except Exception:
        return result

    if not tags:
        return result

    genre_values = tags.get("genre")
    if genre_values:
        result["genre"] = genre_values[0]

    album_values = tags.get("album")
    if album_values:
        result["album"] = album_values[0]

    date_values = tags.get("date")
    if date_values:
        year = _parse_year_from_tag(date_values[0])
        if year is not None:
            result["year"] = year

    bpm_values = tags.get("bpm")
    if bpm_values:
        bpm = _parse_bpm_from_tag(bpm_values[0])
        if bpm is not None:
            result["bpm"] = bpm

    return result


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
    clipped_value = max(float(minimum), min(float(maximum), float(value)))
    return (clipped_value - float(minimum)) / (float(maximum) - float(minimum))


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[int(position)]
    lower_weight = upper_index - position
    upper_weight = position - lower_index
    return sorted_values[lower_index] * lower_weight + sorted_values[upper_index] * upper_weight


# A single mistagged/outlier BPM value (e.g. a corrupt ID3 tag) would
# otherwise stretch bpm_min/bpm_max across the whole library, compressing
# every other track's normalized BPM into a narrow band. Clipping to the
# 5th-95th percentile keeps the scale representative of the bulk of the
# library; _normalize_numeric then clips each track's own value into
# [bpm_min, bpm_max] so true outliers just saturate at 0.0/1.0 instead of
# distorting everyone else.
_BPM_OUTLIER_PERCENTILE = 0.05


def _bpm_bounds(bpm_values: list[float]) -> tuple[float | None, float | None]:
    if not bpm_values:
        return None, None
    sorted_values = sorted(bpm_values)
    bpm_min = _percentile(sorted_values, _BPM_OUTLIER_PERCENTILE)
    bpm_max = _percentile(sorted_values, 1.0 - _BPM_OUTLIER_PERCENTILE)
    if bpm_max <= bpm_min:
        return sorted_values[0], sorted_values[-1]
    return bpm_min, bpm_max


def scan_library(root: str | Path) -> list[TrackRecord]:
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Music library not found: {root_path}")

    tracks: list[TrackRecord] = []
    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue
        artist, title = _split_artist_title(path.stem)
        tag_metadata = _read_tag_metadata(path)
        tracks.append(
            TrackRecord(
                id=_track_id_from_path(path),
                path=str(path.resolve()),
                title=title,
                artist=artist,
                album=tag_metadata.get("album", ""),
                genre=tag_metadata.get("genre", "unknown"),
                bpm=tag_metadata.get("bpm"),
                year=tag_metadata.get("year"),
                enabled=True,
            )
        )
    return tracks


def _build_feature_space(tracks: Iterable[TrackRecord]) -> tuple[list[str], list[str], float | None, float | None, int | None, int | None]:
    genres = sorted({canonicalize_genre(track.genre) for track in tracks} | {"unknown"})
    artists = sorted({_canonicalize_artist(track.artist) for track in tracks} | {"unknown"})

    bpm_values = [track.bpm for track in tracks if track.bpm is not None]
    year_values = [track.year for track in tracks if track.year is not None]

    bpm_min, bpm_max = _bpm_bounds(bpm_values)
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
    bpm_value = [_normalize_numeric(track.bpm, bpm_min, bpm_max)]
    year_value = [_normalize_numeric(track.year, year_min, year_max)]

    # Scale each block by sqrt(weight) so its contribution to a cosine-
    # similarity dot product scales by exactly `weight`. Without this,
    # concatenated one-hot blocks (genre/artist) implicitly dominate over
    # small 0..1 numeric features, and "same artist" alone can trivially
    # produce near-maximal similarity. See FEATURE_WEIGHTS.
    genre_vector = _scale_block(genre_vector, FEATURE_WEIGHTS["genre"])
    artist_vector = _scale_block(artist_vector, FEATURE_WEIGHTS["artist"])
    bpm_value = _scale_block(bpm_value, FEATURE_WEIGHTS["bpm"])
    year_value = _scale_block(year_value, FEATURE_WEIGHTS["year"])

    return genre_vector + artist_vector + bpm_value + year_value


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