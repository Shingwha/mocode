"""Spinner — async animated spinner state machine."""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager

from .theme import DIM, RST, SOFT_CYAN, Spinner, _PRESETS


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
                print(f"\r{DIM}{frame}{suffix}{RST}\033[K", end="", flush=True)
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

    def _clear(self):
        print("\r\033[K", end="", flush=True)
