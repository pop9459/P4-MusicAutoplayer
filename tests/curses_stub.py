"""Minimal stand-in for a curses window, for testing _render_* methods
without a real terminal."""
from __future__ import annotations


class FakeStdscr:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self._size = (height, width)
        self.calls: list[tuple[int, int, str, int]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return self._size

    def erase(self) -> None:
        self.calls.clear()

    def refresh(self) -> None:
        return None

    def timeout(self, ms: int) -> None:
        return None

    def addnstr(self, row: int, col: int, text: str, n: int, attr: int = 0) -> None:
        self.calls.append((row, col, text[:n], attr))

    def addch(self, row: int, col: int, ch: object, attr: int = 0) -> None:
        self.calls.append((row, col, str(ch), attr))
