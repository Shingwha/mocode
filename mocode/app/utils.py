"""Shared utilities for mocode.app — JSON I/O, text shaping, tool grouping."""

from __future__ import annotations

import json
import re
from math import ceil
from pathlib import Path
from shutil import get_terminal_size
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


def ellipsize_tail(text: str, max_width: int) -> str:
    """Truncate at the end: ``'abcdefghij'`` → ``'abcdef...'``."""
    if visible_width(text) <= max_width:
        return text
    if max_width < 4:
        return text[:max_width]
    return text[: max_width - 3] + "..."


def terminal_width(default: int = 80) -> int:
    """Return current terminal column width."""
    w = get_terminal_size((default, 24)).columns
    return w if w > 0 else default


def count_visual_lines(text: str, prompt_width: int) -> int:
    """Count total visual terminal lines *text* occupies.

    Accounts for line wrapping (lines wider than the terminal) and
    double-width CJK characters.  ``prompt_width`` is the column width
    of the prompt prefix on the *first* line (e.g. ``"❯ "`` → 2).
    """
    term_width = terminal_width()

    total = 0
    for i, line in enumerate(text.split("\n")):
        prefix = prompt_width if i == 0 else 0
        line_width = wcswidth(line) if line else 0
        visual = line_width + prefix
        if visual <= 0:
            total += 1  # empty line still occupies one visual row
        else:
            total += ceil(visual / term_width)
    return total


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
