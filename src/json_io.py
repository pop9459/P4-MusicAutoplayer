"""Shared JSON load/save helpers.

Centralizes the read/write pattern duplicated across track_analyzer.py,
library.py, and settings.py: plain UTF-8 JSON, indented + ASCII-escaped with
a trailing newline on write, parent directories created as needed.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: str | Path, data: Any) -> None:
    """Write `data` as JSON, replacing `path` atomically.

    Writing to a temporary file in the same directory and renaming it into
    place means an interrupted write can never leave a half-written file:
    the old contents survive until the new ones are complete. This matters
    because `analyze-bpm` checkpoints the whole library repeatedly during a
    run that takes tens of minutes and is expected to be interrupted.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(data, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
