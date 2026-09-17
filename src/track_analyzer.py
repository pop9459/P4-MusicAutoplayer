from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .json_io import load_json, save_json

try:
    import mutagen
except ImportError:  # pragma: no cover - exercised only when dependency missing
    mutagen = None

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac"}
CATALOG_VERSION = 3
_YEAR_PATTERN = re.compile(r"(\d{4})")

# Relative influence of each metadata signal on recommendation similarity.
# Weights sum to 1.0, and `track_similarity` renormalizes them per pair over
# only the signals both tracks actually carry -- so a library with no BPM tags
# at all scores sanely without retuning anything, and tempo starts pulling its
# full weight the moment the tags exist.
FEATURE_WEIGHTS = {
    "genre": 0.45,
    "bpm": 0.25,
    "artist": 0.20,
    "year": 0.10,
}

# Decay constants for the numeric similarity terms, in the units of the
# feature itself: a gap of this size scores ~0.37, double it ~0.14.
_YEAR_DECAY = 12.0
_BPM_OCTAVE_DECAY = 0.12


# Similarity between two genres that share no label, by how closely the
# grouping in _GENRE_TREE relates them.
_SAME_SUBFAMILY_SIMILARITY = 0.6
_SAME_FAMILY_SIMILARITY = 0.3

# Hand-authored similarity between genre families that are related but not
# nested (symmetric, unordered pairs; unlisted pairs are unrelated). The
# tree already covers "close" -- this covers the handful of cross-family
# bridges a listener would expect, so recommendations aren't confined to one
# branch. Deliberately sparse: only clearly adjacent families are listed.
_FAMILY_ADJACENCY: dict[tuple[str, str], float] = {
    ("rock", "pop"): 0.2,
    ("rock", "roots"): 0.2,
    ("pop", "electronic"): 0.3,
    ("pop", "urban"): 0.3,
    ("urban", "electronic"): 0.2,
    ("jazz", "score"): 0.2,
}


def _genre_similarity(a: TrackFeatures, b: TrackFeatures) -> float | None:
    """Genre closeness in [0, 1], or None when either side is untagged.

    Returning None rather than 0.0 for "unknown" matters: 13% of a real
    library has no genre tag, and treating unknown as a genre of its own made
    every untagged track a perfect genre match for every other untagged track
    -- a 355-track clique that all scored 1.0. Unknown means "not measured",
    so the term is skipped and the other signals decide.
    """
    if a.genre == "unknown" or b.genre == "unknown":
        return None
    if a.genre == b.genre:
        return 1.0
    if a.subfamily is not None and a.subfamily == b.subfamily:
        return _SAME_SUBFAMILY_SIMILARITY
    if a.family is None or b.family is None:
        return 0.0
    if a.family == b.family:
        return _SAME_FAMILY_SIMILARITY
    pair = (a.family, b.family)
    return _FAMILY_ADJACENCY.get(pair, _FAMILY_ADJACENCY.get((b.family, a.family), 0.0))


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


# Two-level grouping over canonical genre labels: label -> (subfamily, family).
#
# Real tag data is a long tail: of 198 labels seen in a 2599-track library, 89
# occur exactly once, and 185 of them match none of the broad families above --
# roughly a fifth of the library sits in labels like "brostep", "hard bass" or
# "indietronica" that are similar to nothing. Collapsing those into their broad
# family would fix the isolation but destroy the distinction (every electronic
# track would be equally similar to every other, and "electronic" is 23% of the
# library). So labels keep their own identity and gain a grouping instead:
# same label > same subfamily > same family > unrelated.
#
# Deliberately NOT listed: "unknown", plus non-genres like "speedrun" and
# "meme". Those should stay isolated rather than being pulled toward anything.
_GENRE_TREE: dict[str, tuple[str, str]] = {
    # electronic
    "electronic": ("electronic", "electronic"),
    "brostep": ("bass", "electronic"),
    "bassline": ("bass", "electronic"),
    "hard bass": ("bass", "electronic"),
    "brazilian bass": ("bass", "electronic"),
    "bass house": ("bass", "electronic"),
    "drum and bass": ("bass", "electronic"),
    "jungle": ("bass", "electronic"),
    "hardstyle": ("hard", "electronic"),
    "hardcore": ("hard", "electronic"),
    "hands up": ("hard", "electronic"),
    "gabber": ("hard", "electronic"),
    "melbourne bounce": ("house", "electronic"),
    "melbourne bounce international": ("house", "electronic"),
    "bounce": ("house", "electronic"),
    "indietronica": ("synth", "electronic"),
    "aussietronica": ("synth", "electronic"),
    "electra": ("synth", "electronic"),
    "synthwave": ("synth", "electronic"),
    "synthpop": ("synth", "electronic"),
    # pop
    "pop": ("pop", "pop"),
    "disco": ("disco", "pop"),
    "hi-nrg": ("disco", "pop"),
    "italo disco": ("disco", "pop"),
    "eurodance": ("disco", "pop"),
    "adult standards": ("standards", "pop"),
    "easy listening": ("standards", "pop"),
    "schlager": ("standards", "pop"),
    # rock
    "rock": ("rock", "rock"),
    # Metal shares rock's subfamily rather than sitting one tier out: the
    # previous hand-authored table put rock/metal at 0.5, closer to the
    # same-subfamily tier than to the same-family one.
    "metal": ("rock", "rock"),
    "grunge": ("alt", "rock"),
    "punk": ("alt", "rock"),
    "post-punk": ("alt", "rock"),
    "indie rock": ("alt", "rock"),
    "new romantic": ("wave", "rock"),
    "new wave": ("wave", "rock"),
    # urban
    "hip-hop": ("hip-hop", "urban"),
    "g funk": ("hip-hop", "urban"),
    "r&b": ("r&b", "urban"),
    "funk": ("r&b", "urban"),
    # world
    "reggae": ("reggae", "world"),
    "latin": ("latin", "world"),
    # roots
    "folk": ("folk", "roots"),
    "country": ("country", "roots"),
    # score
    "classical": ("classical", "score"),
    "soundtrack": ("film", "score"),
    "orchestral soundtrack": ("film", "score"),
    "german soundtrack": ("film", "score"),
    "hollywood": ("film", "score"),
    "theme": ("film", "score"),
    "video game music": ("film", "score"),
    "musique militaire": ("film", "score"),
    # jazz sits in its own family; its closeness to classical comes from the
    # explicit override table rather than from a shared family.
    "jazz": ("jazz", "jazz"),
}

# Fallback for labels not listed above, so a tag this library has never seen
# still lands somewhere sensible instead of becoming another island. Checked
# in order, first substring hit wins, so more specific keywords come first.
_GENRE_FAMILY_KEYWORDS: list[tuple[tuple[str, str], tuple[str, ...]]] = [
    (("bass", "electronic"), ("brostep", "dubstep", "bassline", "bass", "drum and bass", "jungle", "riddim")),
    (("hard", "electronic"), ("hardstyle", "hardcore", "hands up", "gabber", "rawstyle", "frenchcore", "nightcore", "uptempo")),
    (("house", "electronic"), ("house", "bounce", "big room", "garage")),
    (("synth", "electronic"), ("tronica", "synth", "electro", "chiptune", "complextro", "glitch", "phonk", "downtempo")),
    (("electronic", "electronic"), ("techno", "trance", "edm", "rave", "club")),
    (("disco", "pop"), ("disco", "nrg", "eurodance")),
    (("standards", "pop"), ("standards", "easy listening", "schlager", "chanson")),
    (("rock", "rock"), ("metal", "djent")),
    (("alt", "rock"), ("grunge", "punk", "indie rock", "emo")),
    (("wave", "rock"), ("new wave", "new romantic", "darkwave", "permanent wave", "british invasion")),
    (("rock", "rock"), ("rock",)),
    (("hip-hop", "urban"), ("hip hop", "hip-hop", "rap", "trap", "g funk", "drill")),
    (("r&b", "urban"), ("r&b", "rnb", "soul", "funk", "motown")),
    (("reggae", "world"), ("reggae", "dancehall", "ska")),
    (("latin", "world"), ("latin", "reggaeton", "salsa", "cumbia", "bossa", "cubaton", "flamenco", "tropical", "zouk", "balkan")),
    (("folk", "roots"), ("folk", "ludov", "heligonka", "bluegrass")),
    (("country", "roots"), ("country", "honky")),
    (("film", "score"), ("soundtrack", "orchestral", "score", "hollywood", "theme", "video game", "militaire", "movie", "cartoon", "anime", "rhythm game", "tunes")),
    (("classical", "score"), ("classical", "baroque", "opera")),
    (("jazz", "jazz"), ("jazz", "swing", "bebop")),
    (("pop", "pop"), ("pop", "boy band", "idol")),
]


def genre_grouping(label: str) -> tuple[str | None, str | None]:
    """Resolve a canonical genre label to its (subfamily, family).

    Returns (None, None) for labels that should stay isolated -- "unknown"
    and anything that matches no family, such as "speedrun" or "meme".
    """
    if not label or label == "unknown":
        return None, None
    grouping = _GENRE_TREE.get(label)
    if grouping is not None:
        return grouping
    for grouping, keywords in _GENRE_FAMILY_KEYWORDS:
        if any(keyword in label for keyword in keywords):
            return grouping
    return None, None


def _canonicalize_artist(value: str | None) -> str:
    cleaned = _normalize_text(value, default="unknown")
    return cleaned or "unknown"


# Credits arrive as one string: "Skrillex, Boys Noize, Dylan Brady". Treating
# that as an atomic artist made a solo track and a collaboration by the same
# person completely unrelated on the artist axis -- 27% of a real library.
_ARTIST_SEPARATORS = re.compile(
    r"\s*(?:,|&|\bfeaturing\b|\bfeat\.?\b|\bft\.?\b|\bvs\.?\b)\s*",
    re.IGNORECASE,
)


def _artist_keys(artist: str) -> frozenset[str]:
    keys = {part.strip().casefold() for part in _ARTIST_SEPARATORS.split(artist)}
    keys.discard("")
    return frozenset(keys) or frozenset({artist.casefold()})


def _id_from_path(path: Path) -> str:
    """Stable short id derived from a resolved filesystem path.

    Shared by track ids here and folder ids in library.py -- both need the
    same "same resolved path always yields the same id" property.
    """
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest[:16]


def _track_id_from_path(path: Path) -> str:
    return _id_from_path(path)


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

    # `.info` is the container's stream info (length, bitrate, ...), not an
    # easy-tag -- it lives on the same File object easy=True already
    # returned, no second non-easy read needed.
    info = getattr(tags, "info", None)
    duration = getattr(info, "length", None)
    if duration is not None and duration > 0:
        result["duration"] = float(duration)

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
    folder_id: str = ""
    duration: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrackRecord":
        # A pre-v2 catalog carries a precomputed "feature_vector" per track.
        # It is deliberately ignored and dropped on the next save: similarity
        # is now computed per pair from the metadata itself. A pre-v3
        # catalog has no "duration" key at all -- it defaults to None here
        # and gets backfilled on the next rescan, same as a missing bpm tag.
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
            folder_id=str(payload.get("folder_id", "")),
            duration=_coerce_float(payload.get("duration")),
        )


_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_FEAT_TAIL = re.compile(r"\b(?:feat\.?|ft\.?|featuring|with)\b.*$", re.IGNORECASE)
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _base_title(title: str) -> str:
    """Strip a title down to the underlying song, dropping version markers.

    "Thunderstruck", "Thunderstruck (Live)" and "Thunderstruck - Radio Edit"
    are three different tracks that must all stay in the library, but they
    are one song and should never play back to back. Bracketed sections, a
    trailing " - <version>" and a trailing feat. credit are exactly where
    those markers live.
    """
    cleaned = _BRACKETED.sub(" ", title.casefold())
    cleaned = cleaned.split(" - ", 1)[0]
    cleaned = _FEAT_TAIL.sub(" ", cleaned)
    return " ".join(_NON_ALPHANUMERIC.sub(" ", cleaned).split())


def work_key(artist: str, title: str) -> str:
    """Identity of the underlying song, shared by its versions and remixes.

    Keyed on the *lead* credited artist rather than the whole credit string,
    so "Skrillex - Rumble" and "Skrillex, Fred again.. - Rumble" resolve to
    the same work.
    """
    lead = _ARTIST_SEPARATORS.split(artist, maxsplit=1)[0].strip().casefold()
    return f"{lead}|{_base_title(title)}"


@dataclass(slots=True, frozen=True)
class TrackFeatures:
    """Everything `track_similarity` needs about one track, precomputed.

    Derived on load rather than persisted (see `Catalog.track_features`), so
    a canonicalization fix takes effect on the next run instead of requiring
    a full rescan of every tracked folder.
    """

    genre: str
    subfamily: str | None
    family: str | None
    artist_keys: frozenset[str]
    year: int | None
    bpm: float | None
    work_key: str


def _features_for(track: TrackRecord) -> TrackFeatures:
    genre = canonicalize_genre(track.genre)
    subfamily, family = genre_grouping(genre)
    artist = _canonicalize_artist(track.artist)
    return TrackFeatures(
        genre=genre,
        subfamily=subfamily,
        family=family,
        artist_keys=_artist_keys(artist),
        year=track.year,
        bpm=track.bpm,
        work_key=work_key(artist, track.title),
    )


def _artist_similarity(a: TrackFeatures, b: TrackFeatures) -> float | None:
    """Jaccard overlap between two tracks' sets of credited artists.

    Jaccard rather than plain overlap: overlap would score a solo Skrillex
    track and a three-way Skrillex collaboration a perfect 1.0, which just
    re-creates the saturation this is meant to break up. Jaccard's 1/3 says
    "related, not the same", which is what a listener would say too.

    An untagged artist is "not measured", not an artist named unknown, so
    the term is skipped rather than matching every other untagged track.
    """
    unknown = frozenset({"unknown"})
    if not a.artist_keys or not b.artist_keys or a.artist_keys == unknown or b.artist_keys == unknown:
        return None
    intersection = a.artist_keys & b.artist_keys
    if not intersection:
        return 0.0
    return len(intersection) / len(a.artist_keys | b.artist_keys)


def _year_similarity(a: TrackFeatures, b: TrackFeatures) -> float | None:
    """Similarity as a function of the *gap* between two release years.

    Encoding year as a single scaled scalar inside a cosine vector made it a
    recency signal rather than a similarity one: two 2024 tracks scored a huge
    match while two 1960 tracks scored almost nothing for exactly the same
    zero-year gap. An explicit decay over |difference| has no such asymmetry.
    """
    if a.year is None or b.year is None:
        return None
    return math.exp(-abs(a.year - b.year) / _YEAR_DECAY)


def _bpm_similarity(a: TrackFeatures, b: TrackFeatures) -> float | None:
    """Similarity as a circular distance in octaves, so 90 and 180 BPM match.

    Half/double-time is a tagging and beat-detection ambiguity, not a real
    tempo difference -- a track counted at 174 and the same groove counted at
    87 should not sit at opposite ends of the scale. Wrapping the log2 ratio
    makes the two equivalent without folding (and so distorting) the stored
    value, which would otherwise put a seam between 138 and 142 BPM.
    """
    if not a.bpm or not b.bpm or a.bpm <= 0 or b.bpm <= 0:
        return None
    octaves = abs(math.log2(a.bpm / b.bpm)) % 1.0
    distance = min(octaves, 1.0 - octaves)
    return math.exp(-distance / _BPM_OCTAVE_DECAY)


def track_similarity(a: TrackFeatures, b: TrackFeatures) -> float:
    """Weighted similarity in [0, 1] between two tracks' metadata.

    Each term is an independent 0..1 signal; a term where either side has no
    usable data is skipped and the remaining weights are renormalized over
    what is left. That per-pair renormalization is why one weight table works
    whether or not the library has BPM tags: with none, genre/artist/year
    simply share the whole budget between them.
    """
    total_weight = 0.0
    total_score = 0.0
    for name, term in (
        ("genre", _genre_similarity(a, b)),
        ("bpm", _bpm_similarity(a, b)),
        ("year", _year_similarity(a, b)),
        ("artist", _artist_similarity(a, b)),
    ):
        if term is None:
            continue
        weight = FEATURE_WEIGHTS[name]
        total_weight += weight
        total_score += weight * term
    if total_weight == 0.0:
        return 0.0
    return total_score / total_weight


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
    # Per-track precomputed similarity inputs, keyed by track id. Not
    # persisted (see to_dict): deriving it on load costs ~10ms for a
    # 2600-track library and means a canonicalization fix applies on the
    # next run instead of needing every tracked folder rescanned.
    track_features: dict[str, TrackFeatures] = field(default_factory=dict)

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
            track_features=_compute_track_features(tracks),
        )

    def features_for(self, track: TrackRecord) -> TrackFeatures:
        """Cached features for a track, derived on demand if absent.

        A `Catalog` built directly (in tests, or by an older code path) may
        have an empty cache; ranking must still work rather than silently
        skipping every track.
        """
        cached = self.track_features.get(track.id)
        if cached is None:
            cached = _features_for(track)
            self.track_features[track.id] = cached
        return cached

    def to_dict(self) -> dict[str, Any]:
        # "feature_space" is descriptive only since v2 -- similarity no longer
        # depends on a shared vector space, so these are kept for `summary`
        # and for a human reading the file, not consumed by scoring.
        #
        # The version written is always the current one: what gets written is
        # always the current shape, so carrying a loaded v1 file's number
        # through would label an already-migrated file as v1.
        return {
            "version": CATALOG_VERSION,
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


def _scan_one(path: Path, folder_id: str = "") -> TrackRecord:
    artist, title = _split_artist_title(path.stem)
    tag_metadata = _read_tag_metadata(path)
    return TrackRecord(
        id=_track_id_from_path(path),
        path=str(path.resolve()),
        title=title,
        artist=artist,
        album=tag_metadata.get("album", ""),
        genre=tag_metadata.get("genre", "unknown"),
        bpm=tag_metadata.get("bpm"),
        year=tag_metadata.get("year"),
        enabled=True,
        folder_id=folder_id,
        duration=tag_metadata.get("duration"),
    )


def _find_audio_files(root_path: Path) -> list[Path]:
    return [
        path
        for path in sorted(root_path.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    ]


def scan_library(root: str | Path, *, folder_id: str = "") -> list[TrackRecord]:
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Music library not found: {root_path}")

    return [_scan_one(path, folder_id) for path in _find_audio_files(root_path)]


def scan_library_with_progress(
    root: str | Path,
    *,
    folder_id: str = "",
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[TrackRecord]:
    """Same as scan_library, but reports (scanned, total) via progress_callback
    after each file so a caller (e.g. a background thread) can surface scan
    progress for a large folder instead of blocking silently."""
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Music library not found: {root_path}")

    audio_files = _find_audio_files(root_path)
    total = len(audio_files)
    tracks: list[TrackRecord] = []
    for index, path in enumerate(audio_files, start=1):
        tracks.append(_scan_one(path, folder_id))
        if progress_callback is not None:
            progress_callback(index, total)
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


def _compute_track_features(tracks: Iterable[TrackRecord]) -> dict[str, TrackFeatures]:
    return {track.id: _features_for(track) for track in tracks}


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
                folder_id=track.folder_id,
                duration=track.duration,
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
        track_features=_compute_track_features(enriched_tracks),
    )


def load_catalog(path: str | Path) -> Catalog:
    return Catalog.from_dict(load_json(path))


def save_catalog(catalog: Catalog, path: str | Path) -> None:
    save_json(path, catalog.to_dict())