"""Shared utilities for mocode.app — JSON I/O, text shaping, tool grouping."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from wcwidth import wcswidth


# ── Text shaping (moved from cli/textutils.py) ────────────────

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_width(text: str) -> int:
    """Return display width of *text*, ignoring ANSI escape sequences."""
    return wcswidth(_ANSI_RE.sub("", text))


def ellipsize_middle(text: str, max_width: int) -> str:
    """Truncate *text* in the middle: ``'abcdefghij'`` → ``'abcde...hij'``."""
    if visible_width(text) <= max_width:
        return text
    if max_width < 7:  # too narrow for "a...b" — hard truncate
        return text[:max_width]
    head = max_width // 2 - 1
    tail = max_width - head - 3
    return text[:head] + "..." + text[-tail:]


# ── Tool call grouping (moved from cli/display.py) ────────────

_TOOL_KEY = {
    "read": "path",
    "write": "path",
    "append": "path",
    "edit": "path",
    "bash": "command",
    "glob": "pattern",
    "grep": "pattern",
    "fetch": "url",
    "sub_agent": "task",
    "skill": "name",
    "image": "prompt",
    "plan": "action",
}

_MERGE_TOOLS = frozenset({"read", "write", "append", "edit", "glob", "grep"})
_CONTENT_LIMIT = 60  # unified width limit for tool content inside parentheses


def tool_summary(name: str, args: dict) -> str:
    """Extract a short summary string from tool arguments."""
    key = _TOOL_KEY.get(name)
    if not key:
        # Fallback: show the first available argument
        if not args:
            return ""
        key = next(iter(args))
    val = str(args.get(key, ""))
    return ellipsize_middle(val, _CONTENT_LIMIT)


def group_items(
    items: list,
    get_name,
    get_args,
) -> list[tuple[str, list[str]]]:
    """Group items by tool name. Mergeable tools are grouped; others stay individual."""
    merged: dict[str, list[str]] = {}
    singles: list[tuple[str, list[str]]] = []
    for item in items:
        name = get_name(item)
        args = get_args(item)
        summary = tool_summary(name, args)
        if name in _MERGE_TOOLS:
            merged.setdefault(name, []).append(summary)
        else:
            singles.append((name, [summary]))
    return list(merged.items()) + singles


def group_tool_calls(tool_calls) -> list[tuple[str, list[str]]]:
    """Group OpenAI-style tool_calls by name."""
    return group_items(
        tool_calls,
        get_name=lambda tc: tc.name,
        get_args=lambda tc: json.loads(tc.arguments) if isinstance(tc.arguments, str) else (tc.arguments or {}),
    )


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
