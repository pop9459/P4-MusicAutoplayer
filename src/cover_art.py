"""Extract embedded cover art for the currently-playing track and build the
Kitty terminal graphics protocol bytes to display it, entirely locally --
never a network album-art lookup.

Deliberately separate from `track_analyzer.py`: everything that module
reads gets persisted into `TrackRecord`/`data/library.json`, but cover art
is extracted lazily for one track at a time and must never be persisted
(tens of KB per track across a multi-thousand-track catalog would bloat
`library.json` enormously). Keeping it in its own module makes that
boundary structural rather than a rule to remember.

Every function here is pure (or, for `extract_cover_art`, read-only file
I/O with no state/terminal side effects) and tolerant of missing/corrupt
data, same posture as `track_analyzer._read_tag_metadata`: a track with no
art, an unreadable file, or a format mutagen can't parse should never
crash playback -- it should just mean no image is shown.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Mapping

try:
    import mutagen
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:  # pragma: no cover - exercised only when dependency missing
    mutagen = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only when dependency missing
    Image = None

_CHUNK_SIZE = 4096  # max bytes of base64 payload per Kitty APC command


def kitty_graphics_supported(env: Mapping[str, str] | None = None) -> bool:
    """Whether this terminal understands the Kitty graphics protocol.

    Environment-based, not a protocol handshake -- a query/response
    round-trip would need to interleave with curses' own input loop.
    `TERM=xterm-kitty` covers kitty itself; `KITTY_WINDOW_ID` covers
    terminals that implement the protocol but report their own `TERM`
    (e.g. some kitty-protocol-compatible terminals set both anyway, but
    checking both independently is cheap and more robust than either
    alone).
    """
    import os

    if env is None:
        env = os.environ
    return env.get("TERM", "").startswith("xterm-kitty") or bool(env.get("KITTY_WINDOW_ID"))


def extract_cover_art(path: str | Path) -> tuple[bytes, str] | None:
    """Read embedded cover art from an audio file's tags.

    Returns (raw_bytes, mime_type), or None if the file has no embedded
    art, its format isn't one we know how to read art from, or reading it
    fails for any reason (corrupt tags, unsupported variant, etc.) --
    callers should treat None as "no image", never as an error to surface.
    """
    if mutagen is None:
        return None

    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".mp3":
        return _extract_mp3(path)
    if suffix == ".m4a":
        return _extract_mp4(path)
    if suffix == ".flac":
        return _extract_flac(path)
    if suffix == ".ogg":
        return _extract_ogg(path)
    # .wav and bare .aac have no reliable embedded-art convention.
    return None


def _extract_mp3(path: Path) -> tuple[bytes, str] | None:
    try:
        frames = ID3(path).getall("APIC")
        if not frames:
            return None
        return bytes(frames[0].data), str(frames[0].mime)
    except Exception:
        return None


def _extract_mp4(path: Path) -> tuple[bytes, str] | None:
    try:
        tags = MP4(path).tags
        covers = tags.get("covr") if tags else None
        if not covers:
            return None
        cover = covers[0]
        mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        return bytes(cover), mime
    except Exception:
        return None


def _extract_flac(path: Path) -> tuple[bytes, str] | None:
    try:
        pictures = FLAC(path).pictures
        if not pictures:
            return None
        return bytes(pictures[0].data), str(pictures[0].mime)
    except Exception:
        return None


def _extract_ogg(path: Path) -> tuple[bytes, str] | None:
    try:
        tags = mutagen.File(path)
        if tags is None:
            return None
        raw_values = tags.get("metadata_block_picture")
        if not raw_values:
            return None
        picture = Picture(base64.b64decode(raw_values[0]))
        return bytes(picture.data), str(picture.mime)
    except Exception:
        return None


def normalize_to_png(art_bytes: bytes) -> bytes | None:
    """Decode `art_bytes` (whatever format the source track embedded --
    commonly JPEG) and re-encode as PNG, the only format Kitty's graphics
    protocol decodes natively (f=100). Returns None if the bytes aren't a
    decodable image."""
    if Image is None:
        return None
    try:
        image = Image.open(io.BytesIO(art_bytes))
        image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None


def build_kitty_transmit_chunks(png_bytes: bytes, image_id: int, cols: int, rows: int) -> list[bytes]:
    """Build the Kitty graphics protocol APC command(s) to transmit and
    display `png_bytes` as image `image_id`, sized to `cols`x`rows`
    terminal cells (Kitty scales the image to fit -- no pixel-ratio math
    needed on our side).

    Payload is base64-encoded then split into <=4096-byte chunks per the
    protocol's per-command limit. The first chunk carries the full
    control data; a multi-chunk transmission marks every chunk but the
    last with m=1, and the last with m=0 (a single-chunk transmission
    also ends with m=0, so every transmission unambiguously terminates
    the same way).
    """
    payload = base64.b64encode(png_bytes)
    pieces = [payload[i : i + _CHUNK_SIZE] for i in range(0, len(payload), _CHUNK_SIZE)] or [b""]

    chunks: list[bytes] = []
    last_index = len(pieces) - 1
    for index, piece in enumerate(pieces):
        more = index != last_index
        if index == 0:
            control = f"a=T,f=100,c={cols},r={rows},i={image_id},m={1 if more else 0}"
        else:
            control = f"m={1 if more else 0}"
        chunks.append(b"\x1b_G" + control.encode("ascii") + b";" + piece + b"\x1b\\")
    return chunks


def build_kitty_delete(image_id: int) -> bytes:
    """Kitty APC command to delete a previously-transmitted image by id."""
    return f"\x1b_Ga=d,d=i,i={image_id}\x1b\\".encode("ascii")


def build_cursor_position(row: int, col: int) -> bytes:
    """1-indexed ANSI cursor-position sequence (CUP) -- Kitty places a
    transmitted image at the terminal's real cursor position at the
    moment the transmit command is written, which is why this needs to
    move the actual terminal cursor, not curses' internal notion of it."""
    return f"\x1b[{row + 1};{col + 1}H".encode("ascii")
