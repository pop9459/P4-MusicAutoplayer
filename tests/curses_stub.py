"""Minimal stand-in for a curses window, for testing _render_* methods
without a real terminal.

In addition to recording each write as a (row, col, text, attr) tuple in
`calls` (for tests that only care about "was this drawn"), writes are also
composited in call order into a real character grid, so tests can catch
overwrite bugs -- e.g. two draw calls landing on the same cell -- the same
way a real terminal would show them. Use `row_text(row)` to read a
rendered row back out.
"""
from __future__ import annotations


class FakeStdscr:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self._size = (height, width)
        self.calls: list[tuple[int, int, str, int]] = []
        self._grid: list[list[str]] = [[" "] * width for _ in range(height)]

    def getmaxyx(self) -> tuple[int, int]:
        return self._size

    def erase(self) -> None:
        self.calls.clear()
        height, width = self._size
        self._grid = [[" "] * width for _ in range(height)]

    def refresh(self) -> None:
        return None

    def timeout(self, ms: int) -> None:
        return None

    def _write(self, row: int, col: int, text: str) -> None:
        height, width = self._size
        if not (0 <= row < height):
            return
        for offset, ch in enumerate(text):
            c = col + offset
            if 0 <= c < width:
                self._grid[row][c] = ch

    def addnstr(self, row: int, col: int, text: str, n: int, attr: int = 0) -> None:
        text = text[:n]
        self.calls.append((row, col, text, attr))
        self._write(row, col, text)

    def addch(self, row: int, col: int, ch: object, attr: int = 0) -> None:
        # Real curses addch() takes either a one-character string or an
        # integer character code (e.g. an ACS_* constant); mirror that so a
        # test patching in an int-valued ACS_VLINE composites a single cell,
        # not its decimal digits.
        text = chr(ch) if isinstance(ch, int) else str(ch)
        self.calls.append((row, col, text, attr))
        self._write(row, col, text)

    def row_text(self, row: int) -> str:
        return "".join(self._grid[row]).rstrip()
