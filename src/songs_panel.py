"""Middle column: songs from selected folder, selection."""
from __future__ import annotations

from dataclasses import dataclass, field

from .folder_panel import FolderEntry
from .library import ALL_TRACKS_FOLDER_ID, Library
from .track_analyzer import Catalog, TrackRecord


@dataclass
class SongsPanel:
    """Manage song list and selection."""
    catalog: Catalog | None = None
    all_songs: list[TrackRecord] = field(default_factory=list)
    songs: list[TrackRecord] = field(default_factory=list)
    filter_query: str = ""
    selected_index: int = 0
    selected_song: TrackRecord | None = None
    scroll_offset: int = 0
    status_message: str = ""

    def load_songs_from_library(self, library: Library, folder_entry: FolderEntry) -> None:
        """Load enabled songs for the selected folder entry (or every
        enabled track, for the virtual All Tracks entry)."""
        self.catalog = library.catalog
        if folder_entry.id == ALL_TRACKS_FOLDER_ID:
            self.all_songs = [track for track in library.catalog.tracks if track.enabled]
        else:
            self.all_songs = [
                track
                for track in library.catalog.tracks
                if track.enabled and track.folder_id == folder_entry.id
            ]
        self.filter_query = ""
        self._apply_filter_and_sort()
        self.selected_index = 0
        self.selected_song = self.songs[0] if self.songs else None
        self.scroll_offset = 0
        self.status_message = f"{len(self.songs)} songs."

    def _apply_filter_and_sort(self) -> None:
        """Single derivation point for `self.songs`, from `self.all_songs`.
        Filters, and (once sorting lands) sorts -- so both operations
        compose without divergent copies of the same logic."""
        songs = self.all_songs
        if self.filter_query:
            query = self.filter_query.lower()
            songs = [
                track
                for track in songs
                if query in track.title.lower() or query in track.artist.lower()
            ]
        self.songs = songs

    def apply_filter(self, query: str) -> None:
        """Filter the displayed song list by case-insensitive substring
        match against title or artist. Only narrows `self.songs` --
        `self.all_songs` (and the underlying library/catalog) are never
        mutated. An empty query clears the filter."""
        self.filter_query = query.strip()
        self._apply_filter_and_sort()
        self.selected_index = 0
        self.selected_song = self.songs[0] if self.songs else None
        self.scroll_offset = 0
        if self.filter_query:
            self.status_message = f"{len(self.songs)} matches for '{self.filter_query}'."
        else:
            self.status_message = f"{len(self.songs)} songs."

    def select_song(self, index: int) -> None:
        """Select song by index."""
        if 0 <= index < len(self.songs):
            self.selected_index = index
            self.selected_song = self.songs[index]

    def next_song(self) -> None:
        """Move to next song."""
        if self.songs:
            self.selected_index = (self.selected_index + 1) % len(self.songs)
            self.selected_song = self.songs[self.selected_index]
            self._update_scroll()

    def previous_song(self) -> None:
        """Move to previous song."""
        if self.songs:
            self.selected_index = (self.selected_index - 1) % len(self.songs)
            self.selected_song = self.songs[self.selected_index]
            self._update_scroll()

    def _update_scroll(self) -> None:
        """Adjust scroll offset to keep selected song visible."""
        visible_lines = 10  # Approximate, adjust based on actual UI height
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_lines:
            self.scroll_offset = self.selected_index - visible_lines + 1

    def get_visible_songs(self, max_lines: int = 10) -> list[tuple[TrackRecord, int, bool]]:
        """Get visible songs for rendering. Return (track, index, is_selected)."""
        end = min(self.scroll_offset + max_lines, len(self.songs))
        result = []
        for i in range(self.scroll_offset, end):
            is_selected = i == self.selected_index
            result.append((self.songs[i], i, is_selected))
        return result

    def clear(self) -> None:
        """Clear songs (e.g., when switching folders)."""
        self.all_songs = []
        self.songs = []
        self.filter_query = ""
        self.selected_index = 0
        self.selected_song = None
        self.scroll_offset = 0
        self.status_message = "No songs."
