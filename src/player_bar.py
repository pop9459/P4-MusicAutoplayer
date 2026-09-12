"""Bottom bar: minimal player controls."""
from __future__ import annotations

from dataclasses import dataclass

from .track_analyzer import TrackRecord


def _format_time(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}:{secs:02d}"


@dataclass
class PlayerBar:
    """Minimal player bar: track name, play/pause, skip, random."""
    current_track: TrackRecord | None = None
    paused: bool = False
    status_message: str = ""
    time_pos: float | None = None
    duration: float | None = None

    def update_track(self, track: TrackRecord | None) -> None:
        """Update current track display."""
        self.current_track = track
        self.paused = False
        self.time_pos = None
        self.duration = None

    def set_paused(self, paused: bool) -> None:
        """Set pause state."""
        self.paused = paused

    def set_status(self, message: str) -> None:
        """Set temporary status message."""
        self.status_message = message

    def update_progress(self, time_pos: float | None, duration: float | None) -> None:
        """Update current playback position and total track duration."""
        self.time_pos = time_pos
        self.duration = duration

    def get_track_display(self) -> str:
        """Format track for display."""
        if not self.current_track:
            return "[No track]"
        return f"{self.current_track.artist} - {self.current_track.title}"

    def get_state_display(self) -> str:
        """Get play state display."""
        return "PAUSED" if self.paused else "PLAYING"

    def get_progress_display(self, bar_width: int = 20) -> str:
        """Format a fixed-width progress bar and elapsed/total time."""
        if self.time_pos is None or not self.duration:
            return f"[{'-' * bar_width}] --:-- / --:--"

        fraction = max(0.0, min(1.0, self.time_pos / self.duration))
        filled = int(round(fraction * bar_width))
        filled = max(0, min(bar_width, filled))
        bar = "=" * max(0, filled - 1) + (">" if filled else "") + "-" * (bar_width - filled)
        return f"[{bar}] {_format_time(self.time_pos)} / {_format_time(self.duration)}"
