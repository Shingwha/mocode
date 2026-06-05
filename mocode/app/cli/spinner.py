"""Spinner — async animated spinner state machine."""

from __future__ import annotations

import asyncio
import random
import re
import shutil
import time
from contextlib import asynccontextmanager
from math import ceil

from wcwidth import wcswidth

from .theme import DIM, RST, SOFT_CYAN, Spinner, _PRESETS

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI SGR escape sequences for plain-text width measurement."""
    return _ANSI_RE.sub("", text)


def _visual_line_count(text: str) -> int:
    """Count how many terminal lines *text* occupies.

    Accounts for terminal width, line wrapping, and CJK double-width
    characters.  ANSI escape codes in *text* are stripped before
    measurement so styled output is measured correctly.
    """
    stripped = _strip_ansi(text)
    term_width = shutil.get_terminal_size((80, 24)).columns
    if term_width <= 0:
        term_width = 80
    total = 0
    for line in stripped.split("\n"):
        w = wcswidth(line) if line else 0
        if w <= 0:
            total += 1
        else:
            total += ceil(w / term_width)
    return max(total, 1)


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as '1h 3m', '5m 2s', or '47s'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


class SpinnerRunner:
    """Owns the spinner animation lifecycle."""

    def __init__(self) -> None:
        self._active = False
        self._text = ""
        self._start: float = 0.0
        self._detail: str = ""
        self._visual_lines: int = 0

    @property
    def active(self) -> bool:
        return self._active

    def set_text(self, text: str):
        self._text = text.replace("\n", " ")

    def set_detail(self, detail: str):
        self._detail = detail.replace("\n", " ")

    @staticmethod
    def resolve(style: str | Spinner | None = None) -> Spinner:
        """Resolve a spinner style name or object to a Spinner instance."""
        if style is None:
            return random.choice(list(_PRESETS.values()))
        if isinstance(style, Spinner):
            return style
        if style in _PRESETS:
            return _PRESETS[style]
        raise ValueError(
            f"Unknown spinner style: {style!r}. Available: {list(_PRESETS)}"
        )

    @asynccontextmanager
    async def spin(self, text: str = "Thinking", style: str | Spinner | None = None):
        """Shows a spinner while waiting."""
        spinner = self.resolve(style)
        self._active = True
        self._text = text
        self._start = time.monotonic()
        self._detail = ""
        stop = asyncio.Event()
        idx = 0

        def _get_suffix():
            if not spinner.show_text:
                return ""
            elapsed = _format_elapsed(time.monotonic() - self._start)
            elapsed_str = f"{SOFT_CYAN}{elapsed}{RST}"
            if self._detail:
                return f" {self._text}{DIM} · {SOFT_CYAN}{self._detail}{RST} {elapsed_str}"
            return f" {self._text} {elapsed_str}"

        async def _spin():
            nonlocal idx
            while not stop.is_set():
                frame = spinner.frames[idx % len(spinner.frames)]
                suffix = _get_suffix()
                line = f"{DIM}{frame}{suffix}{RST}"
                # Batch: clear previous frame + draw new frame in one write
                buf = self._build_clear_seq()
                buf += f"\r{line}\033[K"
                print(buf, end="", flush=True)
                self._visual_lines = _visual_line_count(line)
                idx += 1
                await asyncio.sleep(spinner.speed)

        task = asyncio.ensure_future(_spin())
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._clear()
            self._active = False

    def _build_clear_seq(self) -> str:
        """Build ANSI sequence to clear all spinner visual lines.

        Returns the escape string without printing it, so callers can
        batch it with other output in a single write.
        """
        n = self._visual_lines
        if n <= 0:
            return ""
        # Bottom-to-top: clear last line, then move-up + clear each preceding
        parts = ["\r\033[K"]
        for _ in range(n - 1):
            parts.append("\033[A\r\033[K")
        return "".join(parts)

    def _clear(self):
        seq = self._build_clear_seq()
        if seq:
            print(seq, end="", flush=True)
        self._visual_lines = 0
