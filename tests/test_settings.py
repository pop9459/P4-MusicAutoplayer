from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.cli import main
from src.settings import load_settings
from src.track_analyzer import TrackRecord, build_catalog, save_catalog


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.settings_path = self.root / "settings.json"
        self.catalog_path = self.root / "catalog.json"
        catalog = build_catalog([
            TrackRecord(id="current", path="/music/current.mp3", title="Current", artist="A", genre="pop", bpm=120, year=2020),
            TrackRecord(id="match", path="/music/match.mp3", title="Match", artist="A", genre="pop", bpm=121, year=2021),
        ])
        save_catalog(catalog, self.catalog_path)
        self.settings_path.write_text(json.dumps({
            "version": 1,
            "catalog_path": "catalog.json",
            "music_directory": "music",
            "top_k": 1,
            "randomness": 0.0,
            "queue_length": 1,
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_load_settings_resolves_paths_relative_to_settings_file(self) -> None:
        settings = load_settings(self.settings_path)

        self.assertEqual(settings.catalog_path, self.catalog_path)
        self.assertEqual(settings.music_directory, self.root / "music")
        self.assertEqual(settings.queue_length, 1)

    def test_load_settings_rejects_invalid_randomness(self) -> None:
        self.settings_path.write_text('{"version": 1, "catalog_path": "catalog.json", "music_directory": "music", "top_k": 1, "randomness": 2, "queue_length": 1}', encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "randomness"):
            load_settings(self.settings_path)

    def test_cli_uses_settings_defaults_and_flags_override_them(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--settings", str(self.settings_path), "queue", "--track-id", "current"]), 0)
        self.assertIn("Generated 1 of 1 requested queue tracks.", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--settings", str(self.settings_path), "queue", "--track-id", "current", "--length", "2"]), 0)
        self.assertIn("Generated 1 of 2 requested queue tracks.", output.getvalue())


if __name__ == "__main__":
    unittest.main()


class ExplicitCliArgumentsTests(unittest.TestCase):
    def test_explicit_arguments_do_not_require_a_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog_path = root / "catalog.json"
            catalog = build_catalog([
                TrackRecord(id="current", path="/music/current.mp3", title="Current", artist="A"),
                TrackRecord(id="next", path="/music/next.mp3", title="Next", artist="B"),
            ])
            save_catalog(catalog, catalog_path)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "--settings", str(root / "missing.json"),
                    "queue",
                    "--catalog", str(catalog_path),
                    "--track-id", "current",
                    "--length", "1",
                    "--top-k", "1",
                    "--randomness", "0.0",
                ]), 0)
            self.assertIn("Generated 1 of 1 requested queue tracks.", output.getvalue())
