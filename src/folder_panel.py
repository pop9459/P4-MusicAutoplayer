"""Left column: tracked-folder list (plus a virtual All Tracks entry), selection."""
from __future__ import annotations

from dataclasses import dataclass, field

from .library import ALL_TRACKS_FOLDER_ID, Library


@dataclass
class FolderEntry:
    """One row in the folder column: either the virtual All Tracks entry
    (id == ALL_TRACKS_FOLDER_ID, path is None) or a real tracked folder."""

    id: str
    display_name: str
    path: str | None
    track_count: int


@dataclass
class FolderPanel:
    """Manage the folder list and selection."""
    entries: list[FolderEntry] = field(default_factory=list)
    selected_index: int = 0
    selected_entry: FolderEntry | None = None
    status_message: str = ""

    def load_from_library(self, library: Library) -> None:
        """Build entries from a Library: a virtual All Tracks entry first,
        then one entry per tracked folder. Preserves the current selection
        by id when possible, else resets to the first entry."""
        previous_id = self.selected_entry.id if self.selected_entry else None

        all_tracks_entry = FolderEntry(
            id=ALL_TRACKS_FOLDER_ID,
            display_name="All Tracks",
            path=None,
            track_count=len(library.catalog.tracks),
        )
        self.entries = [all_tracks_entry] + [
            FolderEntry(
                id=folder.id,
                display_name=folder.display_name,
                path=folder.path,
                track_count=folder.track_count,
            )
            for folder in library.folders
        ]

        new_index = 0
        if previous_id is not None:
            for index, entry in enumerate(self.entries):
                if entry.id == previous_id:
                    new_index = index
                    break
        self.selected_index = new_index
        self.selected_entry = self.entries[new_index] if self.entries else None

    def select_folder(self, index: int) -> None:
        """Select folder by index."""
        if 0 <= index < len(self.entries):
            self.selected_index = index
            self.selected_entry = self.entries[index]

    def next_folder(self) -> None:
        """Move to next folder."""
        if self.entries:
            self.selected_index = (self.selected_index + 1) % len(self.entries)
            self.selected_entry = self.entries[self.selected_index]

    def previous_folder(self) -> None:
        """Move to previous folder."""
        if self.entries:
            self.selected_index = (self.selected_index - 1) % len(self.entries)
            self.selected_entry = self.entries[self.selected_index]
