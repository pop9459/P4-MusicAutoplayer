"""Left column: folder list, selection, scanning."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .settings import Settings
from .track_analyzer import Catalog, build_catalog, load_catalog, save_catalog, scan_library


@dataclass
class FolderPanel:
    """Manage folder list and selection."""
    folders: list[Path] = field(default_factory=list)
    selected_index: int = 0
    selected_folder: Path | None = None
    status_message: str = ""

    def load_folders_from_settings(self, settings: Settings) -> None:
        """Load folder list from settings (music_folders if available, else music_directory)."""
        if settings.music_folders:
            self.folders = list(settings.music_folders)
        else:
            self.folders = [settings.music_directory]
        if self.folders:
            self.selected_folder = self.folders[0]
            self.selected_index = 0

    def select_folder(self, index: int) -> None:
        """Select folder by index."""
        if 0 <= index < len(self.folders):
            self.selected_index = index
            self.selected_folder = self.folders[index]

    def next_folder(self) -> None:
        """Move to next folder."""
        if self.folders:
            self.selected_index = (self.selected_index + 1) % len(self.folders)
            self.selected_folder = self.folders[self.selected_index]

    def previous_folder(self) -> None:
        """Move to previous folder."""
        if self.folders:
            self.selected_index = (self.selected_index - 1) % len(self.folders)
            self.selected_folder = self.folders[self.selected_index]

    def rescan_selected_folder(self, catalog_path: Path) -> Catalog | None:
        """Rescan selected folder and rebuild catalog. Return new catalog or None on error."""
        if not self.selected_folder:
            self.status_message = "No folder selected."
            return None

        try:
            self.status_message = "Scanning..."
            new_catalog = build_catalog(scan_library(self.selected_folder))
            save_catalog(new_catalog, catalog_path)
            self.status_message = "Scanned."
            return new_catalog
        except Exception as error:
            self.status_message = f"Scan failed: {str(error)}"
            return None

    def load_catalog_for_selected_folder(self, catalog_path: Path) -> Catalog | None:
        """Load catalog for selected folder."""
        try:
            return load_catalog(catalog_path)
        except Exception as error:
            self.status_message = f"Load failed: {str(error)}"
            return None
