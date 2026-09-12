"""Tests for the in-TUI settings editor (issue #3)."""
from __future__ import annotations

import unittest
from pathlib import Path

from src.settings import load_settings
from src.settings_panel import FIELDS, SettingsPanel

DEFAULT_SETTINGS = load_settings()


class SettingsPanelHydrationTests(unittest.TestCase):
    def test_load_from_settings_copies_fields(self) -> None:
        panel = SettingsPanel()
        panel.load_from_settings(DEFAULT_SETTINGS)

        self.assertEqual(panel.top_k, DEFAULT_SETTINGS.top_k)
        self.assertEqual(panel.randomness, DEFAULT_SETTINGS.randomness)
        self.assertEqual(panel.queue_length, DEFAULT_SETTINGS.queue_length)
        self.assertEqual(panel.catalog_path, DEFAULT_SETTINGS.catalog_path)
        self.assertEqual(panel.field_index, 0)

    def test_reload_discards_stale_edits(self) -> None:
        panel = SettingsPanel()
        panel.load_from_settings(DEFAULT_SETTINGS)
        panel.increment()
        self.assertNotEqual(panel.top_k, DEFAULT_SETTINGS.top_k)

        panel.load_from_settings(DEFAULT_SETTINGS)
        self.assertEqual(panel.top_k, DEFAULT_SETTINGS.top_k)


class SettingsPanelNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = SettingsPanel()
        self.panel.load_from_settings(DEFAULT_SETTINGS)

    def test_next_field_wraps_around(self) -> None:
        for _ in range(len(FIELDS)):
            self.panel.next_field()
        self.assertEqual(self.panel.field_index, 0)

    def test_previous_field_wraps_around(self) -> None:
        self.panel.previous_field()
        self.assertEqual(self.panel.field_index, len(FIELDS) - 1)

    def test_current_field_matches_index(self) -> None:
        self.assertEqual(self.panel.current_field(), FIELDS[0])
        self.panel.next_field()
        self.assertEqual(self.panel.current_field(), FIELDS[1])


class SettingsPanelAdjustmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = SettingsPanel()
        self.panel.load_from_settings(DEFAULT_SETTINGS)

    def test_top_k_increment_and_decrement(self) -> None:
        self.panel.field_index = FIELDS.index("top_k")
        initial = self.panel.top_k
        self.panel.increment()
        self.assertEqual(self.panel.top_k, initial + 1)
        self.panel.decrement()
        self.assertEqual(self.panel.top_k, initial)

    def test_top_k_floors_at_one(self) -> None:
        self.panel.field_index = FIELDS.index("top_k")
        self.panel.top_k = 1
        self.panel.decrement()
        self.assertEqual(self.panel.top_k, 1)

    def test_queue_length_floors_at_one(self) -> None:
        self.panel.field_index = FIELDS.index("queue_length")
        self.panel.queue_length = 1
        self.panel.decrement()
        self.assertEqual(self.panel.queue_length, 1)

    def test_randomness_clamped_to_unit_interval(self) -> None:
        self.panel.field_index = FIELDS.index("randomness")
        self.panel.randomness = 1.0
        self.panel.increment()
        self.assertEqual(self.panel.randomness, 1.0)
        self.panel.randomness = 0.0
        self.panel.decrement()
        self.assertEqual(self.panel.randomness, 0.0)

    def test_catalog_path_is_not_affected_by_increment(self) -> None:
        self.panel.field_index = FIELDS.index("catalog_path")
        original = self.panel.catalog_path
        self.panel.increment()
        self.assertEqual(self.panel.catalog_path, original)


class SettingsPanelTextEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = SettingsPanel()
        self.panel.load_from_settings(DEFAULT_SETTINGS)

    def test_begin_text_edit_only_applies_to_catalog_path(self) -> None:
        self.panel.field_index = FIELDS.index("top_k")
        self.panel.begin_text_edit()
        self.assertFalse(self.panel.editing_text)

    def test_begin_and_apply_text_edit_updates_catalog_path(self) -> None:
        self.panel.field_index = FIELDS.index("catalog_path")
        self.panel.begin_text_edit()
        self.assertTrue(self.panel.editing_text)

        self.panel.apply_text_edit("new/catalog.json")
        self.assertEqual(self.panel.catalog_path, Path("new/catalog.json"))
        self.assertFalse(self.panel.editing_text)

    def test_apply_text_edit_ignores_blank_value(self) -> None:
        self.panel.field_index = FIELDS.index("catalog_path")
        original = self.panel.catalog_path
        self.panel.begin_text_edit()
        self.panel.apply_text_edit("   ")
        self.assertEqual(self.panel.catalog_path, original)

    def test_cancel_text_edit_clears_flag(self) -> None:
        self.panel.field_index = FIELDS.index("catalog_path")
        self.panel.begin_text_edit()
        self.panel.cancel_text_edit()
        self.assertFalse(self.panel.editing_text)


class SettingsPanelSaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = SettingsPanel()
        self.panel.load_from_settings(DEFAULT_SETTINGS)

    def test_has_changes_false_initially(self) -> None:
        self.assertFalse(self.panel.has_changes())

    def test_has_changes_true_after_edit(self) -> None:
        self.panel.field_index = FIELDS.index("top_k")
        self.panel.increment()
        self.assertTrue(self.panel.has_changes())

    def test_to_settings_reflects_edits_and_preserves_other_fields(self) -> None:
        self.panel.field_index = FIELDS.index("top_k")
        self.panel.increment()

        updated = self.panel.to_settings()

        self.assertEqual(updated.top_k, DEFAULT_SETTINGS.top_k + 1)
        self.assertEqual(updated.music_folders, DEFAULT_SETTINGS.music_folders)
        self.assertEqual(updated.music_directory, DEFAULT_SETTINGS.music_directory)

    def test_to_settings_without_load_raises(self) -> None:
        panel = SettingsPanel()
        with self.assertRaises(ValueError):
            panel.to_settings()


if __name__ == "__main__":
    unittest.main()
