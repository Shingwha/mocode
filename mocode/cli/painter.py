"""Painter — the transcript, as it lands on a terminal.

The only component that knows a screen can be *redrawn*. Everything the
transcript has already committed is printed and forgotten — the terminal's
scrollback is the archive, not us. What stays rewritable is the **live
region**: the rows the current turn's open tool blocks occupy while they run,
each claimed when its call starts and rewritten in place with its verdict.
That is the one region a terminal can update without owning the whole screen,
and it is why a parallel batch stays one row per call, in call order.

Rewriting a row is only sound while the region is the last thing on screen,
so it is guarded rather than trusted: every line in it is clamped to one
terminal row, and the painter checks the display's *epoch* — a count of every
append-style output — before touching a row, so anything printed over the
region freezes it for good. Off a terminal the whole mechanism is off, and a
call simply appends its verdict when it finishes.

Streamed text is deliberately not part of the live region: it is appended as
it streams (the answer is the tallest block on screen, and re-drawing it per
delta is a later, visible change), with deltas a few milliseconds apart
coalesced into one write. Block states map onto that: *open* blocks live in
the region and may update, a finished verdict *seals* its row, and the turn's
rule *commits* the region — printed history is never redrawn.
"""

from __future__ import annotations

import re
import sys
import time
from typing import TYPE_CHECKING

from wcwidth import wcswidth

from .text import terminal_height, terminal_width, visible_width
from .theme import RESET

if TYPE_CHECKING:
    from .display import Display
    from .transcript import Block, Transcript

#: Streamed fragments closer together than this are written as one — a render
#: request may be coalesced, never dropped: whatever is buffered is flushed
#: the moment anything else must land on the screen.
THROTTLE = 0.030

_ANSI_SPLIT = re.compile(r"(\033\[[0-9;]*m)")


def clamp_visible(text: str, max_width: int) -> str:
    """Cut *text* down to *max_width* columns, keeping its escape codes.

    This is the one place the painter truncates instead of letting the
    terminal wrap: a line rewritten in place has to stay exactly one row tall,
    or every row offset in the region after it is wrong. Widths are measured,
    not counted, so CJK text is cut earlier than its character count suggests.
    """
    if max_width <= 0:
        return ""
    if visible_width(text) <= max_width:
        return text

    out: list[str] = []
    width = 0
    for token in _ANSI_SPLIT.split(text):
        if not token:
            continue
        if token.startswith("\033"):
            out.append(token)
            continue
        for char in token:
            step = max(wcswidth(char), 0)
            if width + step > max_width - 1:  # leave room for the ellipsis
                out.append("…")
                out.append(RESET)
                return "".join(out)
            out.append(char)
            width += step
    return "".join(out)


class Painter:
    """Projects the transcript's live region onto the terminal."""

    def __init__(self, display: "Display"):
        self._d = display
        self._live = display.live
        #: block id -> the region row its placeholder owns, when the terminal
        #: can redraw. ``None`` means "nothing on screen to replace".
        self._rows: dict[str, int | None] = {}
        #: How many rows the live region occupies.
        self._region = 0
        #: The display epoch at the region's last own write: any later output
        #: means something else is on screen and the rows are gone.
        self._region_epoch = 0
        #: block id -> the lines of it that are already on screen (a streamed
        #: block's lines are accounted for the moment it opens — they reach
        #: the screen as text, never as lines).
        self._printed: dict[str, list] = {}
        # The stream in flight: which block, how much of its text is written,
        # and what is buffered waiting for the coalescing window to close.
        self._stream_id: str | None = None
        self._stream_kind = ""
        self._streamed = 0
        self._pending = ""
        self._last_flush: float | None = None

    # ── The projection ────────────────────────────────────

    def paint(self, transcript: "Transcript") -> None:
        """Draw what changed in the transcript since the last paint."""
        self._stream_step(transcript)
        for block in transcript.blocks:
            if block.kind == "tool":
                self._tool(block)
            elif block.kind in ("answer", "reasoning"):
                self._streamed_block(block)
            else:
                if self._append(block) and block.kind == "rule":
                    self._reset_region()  # the turn closed: the region is history

    def redraw_all(self, transcript: "Transcript") -> None:
        """Reprint the whole document — the screen is stale, so start over.

        The history-replacement path: committed or not, every block is printed
        once, in order, and the live region — if any survived — is forgotten.
        """
        self._flush_stream(close=True)
        self._rows.clear()
        self._region = 0
        self._region_epoch = self._d.epoch
        self._printed.clear()
        self._d.clear_session()
        self._d.clear_screen()
        for block in transcript.blocks:
            for line in block.lines:
                self._d.render(line)
            self._printed[block.id] = list(block.lines)

    # ── Streamed text ─────────────────────────────────────

    def _stream_step(self, transcript: "Transcript") -> None:
        """Write what is new in the active streaming block, if anything."""
        blocks = transcript.blocks
        active = blocks[-1] if blocks and blocks[-1].state == "streaming" else None
        if active is None:
            self._flush_stream(close=True)  # the block ended: land its tail, close the line
            return
        if active.id != self._stream_id:
            self._flush_stream(close=True)  # a different block: this line is done
            self._stream_id = active.id
            self._stream_kind = active.kind
            self._streamed = 0
        text = active.meta.get("text", "")
        self._pending += text[self._streamed :]
        self._streamed = len(text)
        if self._last_flush is None or time.monotonic() - self._last_flush >= THROTTLE:
            self._flush_stream(close=False)

    def _flush_stream(self, *, close: bool) -> None:
        """Write buffered stream text; when *close*, end the line for good."""
        if self._pending:
            self._d.stream(self._pending, kind=self._stream_kind)
            self._pending = ""
        self._last_flush = time.monotonic()
        if close:
            self._d.end_stream()
            self._stream_id = None
            self._stream_kind = ""
            self._streamed = 0

    def _streamed_block(self, block: "Block") -> None:
        """A streamed block's lines are for readers of the document; the text
        reached the screen as it streamed."""
        self._printed.setdefault(block.id, [])

    # ── Tool blocks: the live region ──────────────────────

    def _tool(self, block: "Block") -> None:
        if block.id in self._printed:
            return  # its verdict already landed
        if block.state == "running":
            if block.id not in self._rows:
                self._rows[block.id] = self._place(block)
            return
        row = self._rows.pop(block.id, None)
        if (
            row is not None
            and self._live
            and row < self._region
            and self._d.epoch == self._region_epoch
        ):
            self._rewrite(row, block.lines[0])
            for line in block.lines[1:]:
                self._d.render(line)
        else:
            # Nothing on screen to replace — off a terminal, frozen, scrolled
            # past, or taller than the screen — so the verdict is appended.
            self._d.render_all(block.lines)
        self._printed[block.id] = list(block.lines)

    def _place(self, block: "Block") -> int | None:
        """Claim a row for a running call, or report that there is none."""
        if self._region and self._d.epoch != self._region_epoch:
            self._reset_region()  # something else printed: those rows are gone
        if not self._live or self._region >= terminal_height() - 1:
            return None
        self._d.print(self._fit(self._d.format(block.lines[0])))
        row = self._region
        self._region += 1
        self._region_epoch = self._d.epoch
        return row

    def _rewrite(self, row: int, line) -> None:
        """Replace the line at *row* in place.

        Moves up to the row, clears it, writes the verdict and returns to the
        bottom of the region — the cursor never leaves the region, which is
        what makes the next rewrite's offsets still correct.
        """
        offset = self._region - row
        text = self._fit(self._d.format(line))
        sys.stdout.write(f"\033[{offset}A\033[K{text}\033[{offset}B\r")
        sys.stdout.flush()

    def _reset_region(self) -> None:
        """Forget the rows: they belong to the scrollback now."""
        self._rows.clear()
        self._region = 0
        self._region_epoch = self._d.epoch

    def _fit(self, text: str) -> str:
        """Clamp to one terminal row — see :func:`clamp_visible`."""
        return clamp_visible(text, max(terminal_width() - 1, 1))

    # ── Everything else: appended ─────────────────────────

    def _append(self, block: "Block") -> bool:
        """Print the lines of a block that does not rewrite itself.

        Returns whether anything was printed. A block whose lines changed
        rather than grew (a plugin block updated in place) is printed again
        rather than rewritten: redrawing open multi-line blocks is a later,
        visible change, and append-only is what a pipe sees in any case.
        """
        printed = self._printed.get(block.id)
        if printed is None:
            fresh = block.lines
        elif block.lines[: len(printed)] == printed:
            fresh = block.lines[len(printed):]  # a follow-up: only what is new
        else:
            fresh = block.lines  # replaced in place: show the update, keep the past
        for line in fresh:
            self._d.render(line)
        if printed is not None or block.lines:
            self._printed[block.id] = list(block.lines)
        return bool(fresh)


__all__ = [
    "Painter",
    "THROTTLE",
    "clamp_visible",
]
