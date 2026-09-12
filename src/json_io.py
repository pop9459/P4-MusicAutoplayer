"""Shared JSON load/save helpers.

Centralizes the read/write pattern duplicated across track_analyzer.py,
library.py, and settings.py: plain UTF-8 JSON, indented + ASCII-escaped with
a trailing newline on write, parent directories created as needed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
