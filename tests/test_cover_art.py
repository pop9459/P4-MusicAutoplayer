"""Tests for src/cover_art.py: cover-art extraction per audio format, PNG
normalization, and the Kitty graphics protocol byte-building. Mirrors
tests/test_track_analyzer.py's ReadTagMetadataTests mocking style -- one
format's mutagen API shape is faked at a time, tolerant of missing/corrupt
data throughout, same posture as _read_tag_metadata.
"""
from __future__ import annotations

import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mutagen.flac import Picture
from PIL import Image

from src.cover_art import (
    build_cursor_position,
    build_kitty_delete,
    build_kitty_transmit_chunks,
    compute_square_cell_box,
    extract_cover_art,
    kitty_graphics_supported,
    normalize_to_png,
    terminal_cell_size_px,
)


class KittyGraphicsSupportedTests(unittest.TestCase):
    def test_term_xterm_kitty_is_supported(self) -> None:
        self.assertTrue(kitty_graphics_supported({"TERM": "xterm-kitty"}))

    def test_kitty_window_id_present_is_supported(self) -> None:
        self.assertTrue(kitty_graphics_supported({"KITTY_WINDOW_ID": "1", "TERM": "xterm-256color"}))

    def test_neither_present_is_unsupported(self) -> None:
        self.assertFalse(kitty_graphics_supported({"TERM": "xterm-256color"}))

    def test_empty_env_is_unsupported(self) -> None:
        self.assertFalse(kitty_graphics_supported({}))


class ExtractCoverArtMp3Tests(unittest.TestCase):
    def test_returns_data_and_mime_from_apic_frame(self) -> None:
        frame = SimpleNamespace(data=b"jpeg-bytes", mime="image/jpeg")
        with patch("src.cover_art.ID3") as mock_id3:
            mock_id3.return_value.getall.return_value = [frame]
            result = extract_cover_art("track.mp3")
        self.assertEqual(result, (b"jpeg-bytes", "image/jpeg"))
        mock_id3.return_value.getall.assert_called_once_with("APIC")

    def test_no_apic_frames_returns_none(self) -> None:
        with patch("src.cover_art.ID3") as mock_id3:
            mock_id3.return_value.getall.return_value = []
            self.assertIsNone(extract_cover_art("track.mp3"))

    def test_corrupt_file_returns_none(self) -> None:
        with patch("src.cover_art.ID3", side_effect=Exception("corrupt")):
            self.assertIsNone(extract_cover_art("track.mp3"))


class _FakeMP4Cover(bytes):
    """Stand-in for mutagen.mp4.MP4Cover: a bytes subclass carrying an
    .imageformat attribute, same shape as the real thing."""


class ExtractCoverArtMp4Tests(unittest.TestCase):
    def test_returns_data_and_jpeg_mime(self) -> None:
        from mutagen.mp4 import MP4Cover

        cover = _FakeMP4Cover(b"jpeg-bytes")
        cover.imageformat = MP4Cover.FORMAT_JPEG
        with patch("src.cover_art.MP4") as mock_mp4:
            mock_mp4.return_value.tags.get.return_value = [cover]
            result = extract_cover_art("track.m4a")
        self.assertEqual(result, (b"jpeg-bytes", "image/jpeg"))

    def test_returns_png_mime(self) -> None:
        from mutagen.mp4 import MP4Cover

        cover = _FakeMP4Cover(b"png-bytes")
        cover.imageformat = MP4Cover.FORMAT_PNG
        with patch("src.cover_art.MP4") as mock_mp4:
            mock_mp4.return_value.tags.get.return_value = [cover]
            result = extract_cover_art("track.m4a")
        self.assertEqual(result, (b"png-bytes", "image/png"))

    def test_no_covr_atom_returns_none(self) -> None:
        with patch("src.cover_art.MP4") as mock_mp4:
            mock_mp4.return_value.tags.get.return_value = None
            self.assertIsNone(extract_cover_art("track.m4a"))

    def test_no_tags_at_all_returns_none(self) -> None:
        with patch("src.cover_art.MP4") as mock_mp4:
            mock_mp4.return_value.tags = None
            self.assertIsNone(extract_cover_art("track.m4a"))

    def test_corrupt_file_returns_none(self) -> None:
        with patch("src.cover_art.MP4", side_effect=Exception("corrupt")):
            self.assertIsNone(extract_cover_art("track.m4a"))


class ExtractCoverArtFlacTests(unittest.TestCase):
    def test_returns_data_and_mime_from_first_picture(self) -> None:
        picture = SimpleNamespace(data=b"flac-art", mime="image/png")
        with patch("src.cover_art.FLAC") as mock_flac:
            mock_flac.return_value.pictures = [picture]
            result = extract_cover_art("track.flac")
        self.assertEqual(result, (b"flac-art", "image/png"))

    def test_no_pictures_returns_none(self) -> None:
        with patch("src.cover_art.FLAC") as mock_flac:
            mock_flac.return_value.pictures = []
            self.assertIsNone(extract_cover_art("track.flac"))

    def test_corrupt_file_returns_none(self) -> None:
        with patch("src.cover_art.FLAC", side_effect=Exception("corrupt")):
            self.assertIsNone(extract_cover_art("track.flac"))


class ExtractCoverArtOggTests(unittest.TestCase):
    """Uses a real mutagen.flac.Picture round-trip (write() then parse via
    the constructor) rather than mocking Picture itself, since this is the
    one extraction path with no real fixture to verify against -- a real
    serialize/deserialize round trip is the next best thing to one."""

    def _encoded_picture_block(self, data: bytes, mime: str) -> str:
        picture = Picture()
        picture.type = 3
        picture.mime = mime
        picture.data = data
        return base64.b64encode(picture.write()).decode("ascii")

    def test_decodes_base64_metadata_block_picture(self) -> None:
        encoded = self._encoded_picture_block(b"ogg-art", "image/jpeg")
        with patch("src.cover_art.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value.get.return_value = [encoded]
            result = extract_cover_art("track.ogg")
        self.assertEqual(result, (b"ogg-art", "image/jpeg"))

    def test_no_picture_field_returns_none(self) -> None:
        with patch("src.cover_art.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value.get.return_value = None
            self.assertIsNone(extract_cover_art("track.ogg"))

    def test_no_tags_object_returns_none(self) -> None:
        with patch("src.cover_art.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value = None
            self.assertIsNone(extract_cover_art("track.ogg"))

    def test_corrupt_base64_returns_none(self) -> None:
        with patch("src.cover_art.mutagen") as mock_mutagen:
            mock_mutagen.File.return_value.get.return_value = ["not valid base64!!"]
            self.assertIsNone(extract_cover_art("track.ogg"))


class ExtractCoverArtUnsupportedFormatTests(unittest.TestCase):
    def test_wav_returns_none_without_touching_mutagen(self) -> None:
        with patch("src.cover_art.mutagen") as mock_mutagen:
            self.assertIsNone(extract_cover_art("track.wav"))
        mock_mutagen.File.assert_not_called()

    def test_aac_returns_none_without_touching_mutagen(self) -> None:
        with patch("src.cover_art.mutagen") as mock_mutagen:
            self.assertIsNone(extract_cover_art("track.aac"))
        mock_mutagen.File.assert_not_called()

    def test_unknown_extension_returns_none(self) -> None:
        self.assertIsNone(extract_cover_art("track.xyz"))


class NormalizeToPngTests(unittest.TestCase):
    @staticmethod
    def _encode(format_name: str) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color=(255, 0, 0)).save(buf, format=format_name)
        return buf.getvalue()

    def test_jpeg_input_normalizes_to_png(self) -> None:
        result = normalize_to_png(self._encode("JPEG"))
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_png_input_stays_png(self) -> None:
        result = normalize_to_png(self._encode("PNG"))
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_garbage_bytes_returns_none(self) -> None:
        self.assertIsNone(normalize_to_png(b"not an image"))


class BuildKittyTransmitChunksTests(unittest.TestCase):
    def test_small_payload_is_a_single_chunk_ending_m0(self) -> None:
        chunks = build_kitty_transmit_chunks(b"x" * 100, image_id=7, cols=10, rows=5)
        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertTrue(chunk.startswith(b"\x1b_G"))
        self.assertTrue(chunk.endswith(b"\x1b\\"))
        header = chunk[3 : chunk.index(b";")].decode("ascii")
        control = dict(pair.split("=") for pair in header.split(","))
        self.assertEqual(control["a"], "T")
        self.assertEqual(control["f"], "100")
        self.assertEqual(control["c"], "10")
        self.assertEqual(control["r"], "5")
        self.assertEqual(control["i"], "7")
        self.assertEqual(control["m"], "0")

    def test_large_payload_splits_into_multiple_chunks(self) -> None:
        # Byte-blind: chunking doesn't care whether this is real image
        # data, only about splitting the base64 text at 4096-byte
        # boundaries -- ~10KB of source bytes base64-encodes to ~13.3KB,
        # comfortably forcing more than one chunk.
        payload = bytes(range(256)) * 40
        chunks = build_kitty_transmit_chunks(payload, image_id=1, cols=8, rows=8)
        self.assertGreater(len(chunks), 1)

        first_header = chunks[0][3 : chunks[0].index(b";")].decode("ascii")
        first_control = dict(pair.split("=") for pair in first_header.split(","))
        self.assertEqual(first_control["m"], "1")
        self.assertIn("a", first_control)
        self.assertIn("i", first_control)

        for middle in chunks[1:-1]:
            header = middle[3 : middle.index(b";")].decode("ascii")
            control = dict(pair.split("=") for pair in header.split(","))
            self.assertEqual(control, {"m": "1"})

        last_header = chunks[-1][3 : chunks[-1].index(b";")].decode("ascii")
        last_control = dict(pair.split("=") for pair in last_header.split(","))
        self.assertEqual(last_control, {"m": "0"})

        # Round trip: concatenating every chunk's base64 payload and
        # decoding it must reproduce the original bytes exactly -- catches
        # off-by-one chunk-boundary bugs.
        import base64 as b64mod

        rebuilt_b64 = b"".join(
            chunk[chunk.index(b";") + 1 : -2] for chunk in chunks
        )
        self.assertEqual(b64mod.b64decode(rebuilt_b64), payload)


class BuildKittyDeleteTests(unittest.TestCase):
    def test_exact_bytes(self) -> None:
        self.assertEqual(build_kitty_delete(1), b"\x1b_Ga=d,d=i,i=1\x1b\\")

    def test_uses_given_id(self) -> None:
        self.assertEqual(build_kitty_delete(42), b"\x1b_Ga=d,d=i,i=42\x1b\\")


class BuildCursorPositionTests(unittest.TestCase):
    def test_origin_is_one_indexed(self) -> None:
        self.assertEqual(build_cursor_position(0, 0), b"\x1b[1;1H")

    def test_nonzero_row_col(self) -> None:
        self.assertEqual(build_cursor_position(5, 10), b"\x1b[6;11H")


class TerminalCellSizePxTests(unittest.TestCase):
    def _fake_winsize(self, rows: int, cols: int, xpixel: int, ypixel: int) -> bytes:
        import struct

        return struct.pack("HHHH", rows, cols, xpixel, ypixel)

    def test_returns_pixel_size_per_cell(self) -> None:
        with patch("src.cover_art.fcntl.ioctl", return_value=self._fake_winsize(24, 80, 800, 480)):
            result = terminal_cell_size_px(1)
        self.assertEqual(result, (10.0, 20.0))

    def test_ioctl_error_returns_none(self) -> None:
        with patch("src.cover_art.fcntl.ioctl", side_effect=OSError("not a tty")):
            self.assertIsNone(terminal_cell_size_px(1))

    def test_zero_pixel_fields_returns_none(self) -> None:
        """Some terminals report a valid cell grid but leave the pixel
        fields at 0 -- must not divide by zero, must signal "unknown"."""
        with patch("src.cover_art.fcntl.ioctl", return_value=self._fake_winsize(24, 80, 0, 0)):
            self.assertIsNone(terminal_cell_size_px(1))

    def test_zero_rows_or_cols_returns_none(self) -> None:
        with patch("src.cover_art.fcntl.ioctl", return_value=self._fake_winsize(0, 0, 800, 480)):
            self.assertIsNone(terminal_cell_size_px(1))


class ComputeSquareCellBoxTests(unittest.TestCase):
    def test_width_constrained_box_shrinks_to_square(self) -> None:
        # Cell is 10x20px (2:1 height:width, a typical monospace ratio).
        # A wide box (40 cols) with only 8 rows available is height-
        # constrained: 8 rows * 20px = 160px tall, so the square side is
        # 160px, i.e. 16 cols wide -- not the full 40.
        cols, rows = compute_square_cell_box(40, 8, 10.0, 20.0)
        self.assertEqual(rows, 8)
        self.assertEqual(cols, 16)

    def test_height_constrained_box_shrinks_to_square(self) -> None:
        # Narrow box (5 cols) with lots of rows available is width-
        # constrained: 5 cols * 10px = 50px wide, so the square side is
        # 50px, i.e. 2.5 -> 2 rows tall.
        cols, rows = compute_square_cell_box(5, 20, 10.0, 20.0)
        self.assertEqual(cols, 5)
        self.assertEqual(rows, 2)

    def test_result_is_actually_square_in_pixels(self) -> None:
        cols, rows = compute_square_cell_box(30, 10, 9.0, 18.0)
        width_px = cols * 9.0
        height_px = rows * 18.0
        # Within one cell's worth of rounding error in each dimension.
        self.assertLess(abs(width_px - height_px), max(9.0, 18.0))

    def test_zero_dimensions_fall_back_to_bounds(self) -> None:
        self.assertEqual(compute_square_cell_box(0, 10, 10.0, 20.0), (1, 10))
        self.assertEqual(compute_square_cell_box(10, 0, 10.0, 20.0), (10, 1))

    def test_zero_cell_size_falls_back_to_bounds(self) -> None:
        self.assertEqual(compute_square_cell_box(10, 8, 0.0, 20.0), (10, 8))


if __name__ == "__main__":
    unittest.main()
