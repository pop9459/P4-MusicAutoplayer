"""Settings screen: edit a copy of Settings in-place, save via settings.save_settings."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from .settings import Settings

FIELDS = ("top_k", "randomness", "queue_length", "catalog_path")

RANDOMNESS_STEP = 0.05


@dataclass
class SettingsPanel:
    """Editable copy of the fields a user can change without hand-editing
    settings.json. Numeric fields adjust with +/-; `catalog_path` is edited
    as free text. `music_folders` is shown read-only when there is more than
    one folder -- editing an N-item path list via a curses line editor is
    out of scope."""

    original: Settings | None = None
    top_k: int = 1
    randomness: float = 0.0
    queue_length: int = 1
    catalog_path: Path = field(default_factory=Path)
    music_folders: tuple[Path, ...] = ()
    field_index: int = 0
    editing_text: bool = False
    text_buffer: str = ""
    status_message: str = ""

    def load_from_settings(self, settings: Settings) -> None:
        """Hydrate all fields fresh from `settings`, discarding any stale edits."""
        self.original = settings
        self.top_k = settings.top_k
        self.randomness = settings.randomness
        self.queue_length = settings.queue_length
        self.catalog_path = settings.catalog_path
        self.music_folders = settings.music_folders or ()
        self.field_index = 0
        self.editing_text = False
        self.text_buffer = ""
        self.status_message = ""

    def next_field(self) -> None:
        self.field_index = (self.field_index + 1) % len(FIELDS)

    def previous_field(self) -> None:
        self.field_index = (self.field_index - 1) % len(FIELDS)

    def current_field(self) -> str:
        return FIELDS[self.field_index]

    def increment(self) -> None:
        field_name = self.current_field()
        if field_name == "top_k":
            self.top_k += 1
        elif field_name == "randomness":
            self.randomness = round(min(1.0, self.randomness + RANDOMNESS_STEP), 2)
        elif field_name == "queue_length":
            self.queue_length += 1

    def decrement(self) -> None:
        field_name = self.current_field()
        if field_name == "top_k":
            self.top_k = max(1, self.top_k - 1)
        elif field_name == "randomness":
            self.randomness = round(max(0.0, self.randomness - RANDOMNESS_STEP), 2)
        elif field_name == "queue_length":
            self.queue_length = max(1, self.queue_length - 1)

    def begin_text_edit(self) -> None:
        """Start editing `catalog_path` as free text (only editable text field)."""
        if self.current_field() != "catalog_path":
            return
        self.editing_text = True
        self.text_buffer = str(self.catalog_path)

    def apply_text_edit(self, value: str) -> None:
        value = value.strip()
        if value:
            self.catalog_path = Path(value)
        self.editing_text = False
        self.text_buffer = ""

    def cancel_text_edit(self) -> None:
        self.editing_text = False
        self.text_buffer = ""

    def has_changes(self) -> bool:
        if self.original is None:
            return False
        return (
            self.top_k != self.original.top_k
            or self.randomness != self.original.randomness
            or self.queue_length != self.original.queue_length
            or self.catalog_path != self.original.catalog_path
        )

    def to_settings(self) -> Settings:
        if self.original is None:
            raise ValueError("SettingsPanel has no original Settings loaded")
        return replace(
            self.original,
            top_k=self.top_k,
            randomness=self.randomness,
            queue_length=self.queue_length,
            catalog_path=self.catalog_path,
        )
