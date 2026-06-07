"""Shared utilities for mocode.app — JSON I/O helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(
    path: Path | str,
    *,
    encoding: str = "utf-8",
) -> dict[str, Any] | None:
    """Read a JSON file and return its contents as a dict.

    Returns ``None`` if the file doesn't exist, is not valid JSON,
    or cannot be read (OS-level errors).
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding=encoding))
    except (json.JSONDecodeError, OSError):
        return None


def write_json(
    path: Path | str,
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    encoding: str = "utf-8",
) -> None:
    """Write *data* as JSON to *path*, creating parent directories as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, indent=indent, ensure_ascii=ensure_ascii),
        encoding=encoding,
    )
