"""Middle column: songs from selected folder, selection."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from .folder_panel import FolderEntry
from .library import ALL_TRACKS_FOLDER_ID, Library
from .track_analyzer import Catalog, TrackRecord


def _normalize_for_search(text: str) -> str:
    """Fold to lowercase and strip diacritics (NFKD decomposition, drop
    combining marks), so a plain "a" query matches "á", "e" matches "é",
    etc. -- most keyboards have no easy way to type the accented form, so
    requiring it to search would make half a library's non-English tags
    unsearchable."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


SORT_MODES = ("default", "title", "artist", "album")

_SORT_KEY_FNS = {
    "title": lambda track: (track.title.lower(), track.artist.lower()),
    "artist": lambda track: (track.artist.lower(), track.title.lower()),
    "album": lambda track: (track.album.lower(), track.title.lower()),
}


@dataclass
class SongsPanel:
    """Manage song list and selection."""
    catalog: Catalog | None = None
    all_songs: list[TrackRecord] = field(default_factory=list)
    songs: list[TrackRecord] = field(default_factory=list)
    filter_query: str = ""
    sort_mode: str = "default"
    selected_index: int = 0
    selected_song: TrackRecord | None = None
    scroll_offset: int = 0
    status_message: str = ""
    # Kept in sync with the real rendered viewport height each render tick
    # (see Player3Column._render_songs), so _update_scroll's centering
    # matches the terminal's actual size rather than a guess.
    visible_lines: int = 10

    def load_songs_from_library(self, library: Library, folder_entry: FolderEntry) -> None:
        """Load enabled songs for the selected folder entry (or every
        enabled track, for the virtual All Tracks entry). `sort_mode` is a
        display preference, not folder-scoped like `filter_query` -- it
        carries over across folder switches."""
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
        Filters, then sorts -- so both operations compose without
        divergent copies of the same logic."""
        songs = self.all_songs
        if self.filter_query:
            query = _normalize_for_search(self.filter_query)
            songs = [
                track
                for track in songs
                if query in _normalize_for_search(track.title)
                or query in _normalize_for_search(track.artist)
            ]
        key_fn = _SORT_KEY_FNS.get(self.sort_mode)
        if key_fn is not None:
            songs = sorted(songs, key=key_fn)
        self.songs = songs

    def _reselect_by_identity(self, track: TrackRecord | None) -> None:
        """Re-select `track` by id after `self.songs` has been
        re-derived (filter or sort), so the user's selection follows the
        track rather than silently landing on whatever now occupies its
        old numeric index. Falls back to the top of the list if the track
        no longer appears (e.g. filtered out)."""
        if track is not None:
            for index, candidate in enumerate(self.songs):
                if candidate.id == track.id:
                    self.selected_index = index
                    self.selected_song = candidate
                    self.scroll_offset = min(self.scroll_offset, index)
                    return
        self.selected_index = 0
        self.selected_song = self.songs[0] if self.songs else None
        self.scroll_offset = 0

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

    def cycle_sort(self) -> None:
        """Advance to the next sort mode (wrapping back to "default"),
        preserving the currently-selected track's identity across the
        reorder rather than its numeric index."""
        current_track = self.selected_song
        next_index = (SORT_MODES.index(self.sort_mode) + 1) % len(SORT_MODES)
        self.sort_mode = SORT_MODES[next_index]
        self._apply_filter_and_sort()
        self._reselect_by_identity(current_track)
        self.status_message = f"Sorted by {self.sort_mode}."

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
        """Keep the selected row centered in the visible viewport
        (scrolloff-style), rather than letting the cursor ride the top/
        bottom edge before scrolling kicks in. Clamped at both ends: near
        the top of the list scroll_offset floors at 0 (can't scroll past
        the start), near the bottom it floors at the last full page (can't
        scroll past the end) -- true centering only happens away from
        either edge, same as vim's `scrolloff=999`."""
        if not self.songs:
            self.scroll_offset = 0
            return
        half = self.visible_lines // 2
        max_offset = max(0, len(self.songs) - self.visible_lines)
        self.scroll_offset = max(0, min(self.selected_index - half, max_offset))

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
