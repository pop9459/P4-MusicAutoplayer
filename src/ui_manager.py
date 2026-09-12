"""Tab manager and key dispatch for the new TUI player."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

TAB_PLAYER = "player"
TAB_QUEUE = "queue"
TAB_FOLDERS = "folders"
TAB_SETTINGS = "settings"

TABS = [TAB_PLAYER, TAB_QUEUE, TAB_FOLDERS, TAB_SETTINGS]


@dataclass
class UIState:
    """Tracks the current tab and global UI state."""
    active_tab: str = TAB_PLAYER
    paused: bool = False
    status_message: str = ""
    folders_edit_mode: bool = False
    settings_edit_mode: bool = False

    def next_tab(self) -> None:
        """Move to the next tab."""
        current_index = TABS.index(self.active_tab)
        self.active_tab = TABS[(current_index + 1) % len(TABS)]

    def previous_tab(self) -> None:
        """Move to the previous tab."""
        current_index = TABS.index(self.active_tab)
        self.active_tab = TABS[(current_index - 1) % len(TABS)]

    def set_tab(self, tab_name: str) -> None:
        """Set the active tab directly."""
        if tab_name in TABS:
            self.active_tab = tab_name


class UIManager:
    """Manages tab state, view rendering, and key dispatch."""

    def __init__(self) -> None:
        self.state = UIState()
        self.handlers: dict[str, dict[int, Callable[[UIManager], None]]] = {
            TAB_PLAYER: {},
            TAB_QUEUE: {},
            TAB_FOLDERS: {},
            TAB_SETTINGS: {},
        }

    def register_handler(self, tab: str, key: int, handler: Callable[[UIManager], None]) -> None:
        """Register a key handler for a specific tab."""
        if tab not in self.handlers:
            self.handlers[tab] = {}
        self.handlers[tab][key] = handler

    def dispatch_key(self, key: int) -> bool:
        """Dispatch a key to the current tab's handlers.

        Global keys (Tab, Shift+Tab, q) are handled first.
        Returns True if the key was handled, False otherwise.
        """
        # Global keys
        if key == ord("\t"):
            self.state.next_tab()
            self.state.status_message = ""
            return True
        # Shift+Tab is not directly available, but we can use Esc+Tab or similar
        # For now, we'll handle it in the main loop
        if key in (ord("q"), ord("Q")):
            return False  # Signal to quit

        # Tab-specific handlers
        handlers = self.handlers.get(self.state.active_tab, {})
        if key in handlers:
            handlers[key](self)
            return True

        return False
