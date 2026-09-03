from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.cli import main
from src.track_analyzer import TrackRecord, build_catalog, save_catalog


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temporary_directory.name) / "tracks.json"
        catalog = build_catalog([
            TrackRecord(id="current", path="/music/current.mp3", title="Current", artist="A", genre="pop", bpm=120, year=2020),
            TrackRecord(id="match", path="/music/match.mp3", title="Match", artist="A", genre="pop", bpm=121, year=2021),
            TrackRecord(id="disabled", path="/music/disabled.mp3", title="Disabled", artist="A", genre="pop", bpm=120, year=2020, enabled=False),
        ])
        save_catalog(catalog, self.catalog_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _run(self, *arguments: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        return output.getvalue()

    def test_summary_displays_catalog_statistics(self) -> None:
        output = self._run("summary", "--catalog", str(self.catalog_path))
        self.assertIn("Tracks: 3 (2 enabled, 1 disabled)", output)
        self.assertIn("Feature vector size:", output)

    def test_list_tracks_honors_enabled_only(self) -> None:
        output = self._run("list-tracks", "--catalog", str(self.catalog_path), "--enabled-only")
        self.assertIn("current", output)
        self.assertNotIn("disabled  [disabled]", output)

    def test_recommendation_displays_ranked_eligible_candidates(self) -> None:
        output = self._run("recommend", "--catalog", str(self.catalog_path), "--track-id", "current")
        self.assertIn("Eligible candidates analyzed: 1", output)
        self.assertIn("match", output)
        self.assertNotIn("disabled  [disabled]", output)
        self.assertIn("Recommendation", output)


if __name__ == "__main__":
    unittest.main()
