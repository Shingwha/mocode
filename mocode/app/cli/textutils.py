"""CLI text utilities — display width, line counting, truncation."""

from __future__ import annotations

import re
from math import ceil
from shutil import get_terminal_size

from wcwidth import wcswidth

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


def terminal_width(default: int = 80) -> int:
    """Return current terminal column width."""
    w = get_terminal_size((default, 24)).columns
    return w if w > 0 else default
