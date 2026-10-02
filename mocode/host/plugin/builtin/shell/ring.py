"""The bounded output buffer a background job drains its pipes into."""

from __future__ import annotations

import re
from collections import deque


class _Ring:
    """A bounded buffer of lines — keeps the newest, counts what it dropped.

    The memory bound for background output: at most *max_lines* lines and
    *max_bytes* characters, whichever is hit first. A dev server running
    overnight overflows either way; the answer is to drop the oldest and say
    how many went, not to grow without bound.
    """

    def __init__(self, max_lines: int = 2000, max_bytes: int = 256 * 1024):
        self.lines: deque[str] = deque()
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self._bytes = 0
        #: Lines evicted before anything could read them.
        self.discarded = 0

    def append(self, line: str) -> None:
        self.lines.append(line)
        self._bytes += len(line)
        while self.lines and (
            len(self.lines) > self.max_lines or self._bytes > self.max_bytes
        ):
            dropped = self.lines.popleft()
            self._bytes -= len(dropped)
            self.discarded += 1

    def drain(self) -> list[str]:
        """Take everything buffered — a plain incremental read."""
        lines = list(self.lines)
        self.lines.clear()
        self._bytes = 0
        return lines

    def take_matching(self, pattern: re.Pattern) -> list[str]:
        """Take the lines matching *pattern*, keep the rest buffered.

        A filtered read consumes only what it matched: the unmatched lines
        stay for a later read (plain, or with another pattern) rather than
        being lost — the caller said what it wanted, not what to throw away.
        """
        matched: list[str] = []
        kept: deque[str] = deque()
        for line in self.lines:
            if pattern.search(line):
                matched.append(line)
            else:
                kept.append(line)
        self.lines = kept
        self._bytes = sum(len(line) for line in kept)
        return matched
