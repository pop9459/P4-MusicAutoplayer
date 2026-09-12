"""Bottom bar: minimal player controls."""
from __future__ import annotations

from dataclasses import dataclass

from .track_analyzer import TrackRecord


@dataclass
class PlayerBar:
    """Minimal player bar: track name, play/pause, skip, random."""
    current_track: TrackRecord | None = None
    paused: bool = False
    status_message: str = ""

    def update_track(self, track: TrackRecord | None) -> None:
        """Update current track display."""
        self.current_track = track
        self.paused = False

    def set_paused(self, paused: bool) -> None:
        """Set pause state."""
        self.paused = paused

    def set_status(self, message: str) -> None:
        """Set temporary status message."""
        self.status_message = message

    def get_track_display(self) -> str:
        """Format track for display."""
        if not self.current_track:
            return "[No track]"
        return f"{self.current_track.artist} - {self.current_track.title}"

    def get_state_display(self) -> str:
        """Get play state display."""
        return "PAUSED" if self.paused else "PLAYING"
