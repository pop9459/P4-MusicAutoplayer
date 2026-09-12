"""Right column: upcoming queue display."""
from __future__ import annotations

from dataclasses import dataclass, field

from .track_analyzer import TrackRecord


@dataclass
class QueuePanel:
    """Display upcoming queue."""
    queue: list[TrackRecord] = field(default_factory=list)
    scroll_offset: int = 0
    status_message: str = ""

    def update_queue(self, queue: list[TrackRecord]) -> None:
        """Update displayed queue."""
        self.queue = queue
        self.scroll_offset = 0
        self.status_message = f"Queue ({len(queue)})"

    def scroll_down(self) -> None:
        """Scroll queue view down."""
        if self.scroll_offset + 1 < len(self.queue):
            self.scroll_offset += 1

    def scroll_up(self) -> None:
        """Scroll queue view up."""
        if self.scroll_offset > 0:
            self.scroll_offset -= 1

    def get_visible_queue(self, max_lines: int = 10) -> list[tuple[TrackRecord, int, bool]]:
        """Get visible queue items for rendering. Return (track, index, is_first)."""
        end = min(self.scroll_offset + max_lines, len(self.queue))
        result = []
        for i in range(self.scroll_offset, end):
            is_first = i == 0
            result.append((self.queue[i], i, is_first))
        return result

    def clear(self) -> None:
        """Clear queue."""
        self.queue = []
        self.scroll_offset = 0
        self.status_message = "Empty"
