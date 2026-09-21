"""Right column: upcoming queue display."""
from __future__ import annotations

from dataclasses import dataclass, field

from .track_analyzer import TrackRecord


@dataclass
class QueuePanel:
    """Display upcoming queue."""
    queue: list[TrackRecord] = field(default_factory=list)
    current_track: TrackRecord | None = None
    status_message: str = ""

    def update_queue(self, queue: list[TrackRecord], current_track: TrackRecord | None = None) -> None:
        """Update displayed queue."""
        self.queue = queue
        self.current_track = current_track
        self.status_message = f"Queue ({len(queue)})"

    def get_visible_queue(self, max_lines: int = 10) -> list[tuple[TrackRecord, int]]:
        """Get visible queue items for rendering. Return (track, index)."""
        return [(track, index) for index, track in enumerate(self.queue[:max_lines])]
