"""Middle column: songs from selected folder, selection."""
from __future__ import annotations

from dataclasses import dataclass, field

from .track_analyzer import Catalog, TrackRecord


@dataclass
class SongsPanel:
    """Manage song list and selection."""
    catalog: Catalog | None = None
    songs: list[TrackRecord] = field(default_factory=list)
    selected_index: int = 0
    selected_song: TrackRecord | None = None
    scroll_offset: int = 0
    status_message: str = ""

    def load_songs_from_catalog(self, catalog: Catalog) -> None:
        """Load songs from catalog, extract enabled tracks."""
        self.catalog = catalog
        self.songs = [track for track in catalog.tracks if track.enabled]
        self.selected_index = 0
        self.selected_song = self.songs[0] if self.songs else None
        self.scroll_offset = 0
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
        self.songs = []
        self.selected_index = 0
        self.selected_song = None
        self.scroll_offset = 0
        self.status_message = "No songs."
