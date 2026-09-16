"""Text utilities — terminal width, ellipsizing, and byte decoding."""

from __future__ import annotations

import re
from math import ceil
from shutil import get_terminal_size

from wcwidth import wcswidth

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_width(text: str) -> int:
    """Display width of *text*, ignoring ANSI escape sequences."""
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
    """Current terminal column width."""
    width = get_terminal_size((default, 24)).columns
    return width if width > 0 else default


def count_visual_lines(text: str, prompt_width: int) -> int:
    """Count the terminal rows *text* occupies, accounting for wrapping and CJK.

    ``prompt_width`` is the column width of the prompt prefix on the first line.
    """
    term_width = terminal_width()
    total = 0
    for i, line in enumerate(text.split("\n")):
        prefix = prompt_width if i == 0 else 0
        line_width = wcswidth(line) if line else 0
        visual = line_width + prefix
        if visual <= 0:
            total += 1  # an empty line still occupies one row
        else:
            total += ceil(visual / term_width)
    return total


def decode_bytes(data: bytes) -> str:
    """Decode bytes to string, trying the encodings MoCode meets in practice."""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936", "gb2312"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
