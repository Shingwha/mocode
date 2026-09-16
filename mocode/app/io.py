"""JSON persistence helpers — best-effort reads, atomic-ish writes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path | str, *, encoding: str = "utf-8") -> dict[str, Any] | None:
    """Read a JSON file.

    Returns ``None`` when the file is missing, unreadable, or not valid JSON.
    """
    p = Path(path)
    if not p.is_file():
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
    """Write *data* as JSON, creating parent directories as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, indent=indent, ensure_ascii=ensure_ascii),
        encoding=encoding,
    )
