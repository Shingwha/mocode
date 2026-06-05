"""Spinner — async animated spinner state machine."""

from __future__ import annotations

import asyncio
import random
import re
import time
from contextlib import asynccontextmanager
from shutil import get_terminal_size
from wcwidth import wcswidth

from .theme import DIM, RST, SOFT_CYAN, Spinner, _PRESETS

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_width(text: str) -> int:
    """Return display width of *text*, ignoring ANSI escape sequences."""
    return wcswidth(_ANSI_RE.sub("", text))


def _ellipsize_middle(text: str, max_width: int) -> str:
    """Truncate *text* in the middle: ``'abcdefghij'`` → ``'abcde...hij'``."""
    if _visible_width(text) <= max_width:
        return text
    if max_width < 7:  # too narrow for "a...b" — hard truncate
        return text[:max_width]
    head = max_width // 2 - 1
    tail = max_width - head - 3
    return text[:head] + "..." + text[-tail:]


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

        def _get_suffix(max_suffix_width: int):
            if not spinner.show_text:
                return ""
            elapsed = _format_elapsed(time.monotonic() - self._start)
            elapsed_str = f"{SOFT_CYAN}{elapsed}{RST}"
            elapsed_vis = len(elapsed)

            if self._detail:
                # Fixed: " " + " · " + " " + elapsed = 5 + elapsed_vis
                fixed = 5 + elapsed_vis
                text_vis = _visible_width(self._text)
                remaining = max_suffix_width - fixed - text_vis
                if remaining < _visible_width(self._detail):
                    detail = _ellipsize_middle(self._detail, max(4, remaining))
                else:
                    detail = self._detail
                return f" {self._text}{DIM} · {SOFT_CYAN}{detail}{RST} {elapsed_str}"

            avail = max_suffix_width - elapsed_vis - 1
            if _visible_width(self._text) > avail:
                text = _ellipsize_middle(self._text, max(4, avail))
            else:
                text = self._text
            return f" {text} {elapsed_str}"

        async def _spin():
            nonlocal idx
            frame_interval = spinner.speed
            text_interval = 0.05
            last_frame = time.monotonic()
            frame = spinner.frames[0]

            while not stop.is_set():
                now = time.monotonic()
                if now - last_frame >= frame_interval:
                    idx += 1
                    frame = spinner.frames[idx % len(spinner.frames)]
                    last_frame = now

                frame_vis = _visible_width(frame)
                term_w = get_terminal_size((80, 24)).columns
                max_suffix = max(10, int(term_w * 0.9) - 15)
                suffix = _get_suffix(max_suffix)
                print(f"\r{DIM}{frame}{suffix}{RST}\033[K", end="", flush=True)
                await asyncio.sleep(text_interval)

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

    def _clear(self):
        print("\r\033[K", end="", flush=True)
