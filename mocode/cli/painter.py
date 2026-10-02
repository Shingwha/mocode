"""Painter — the transcript, as it lands on a terminal.

The only component that knows a screen can be *redrawn*. Everything the
transcript has already committed is printed and forgotten — the terminal's
scrollback is the archive, not us. What stays rewritable is the **live
region**: the rows the current turn's open blocks occupy — running tools
with their output tail, a streaming answer's last unfinished line, the
thinking spinner while nothing else is on stage. Any of them changing is
one repaint of the region: move up to its top, rewrite row by row, add the
rows it grew or delete the rows it shed.

Rows are addressed in *visual* lines — a row that wraps is split before it
enters the region, so an offset of one corrupts nothing. A row that is
final (a landed verdict, a completed stream line) is never changed again,
only re-written with itself while rows below it still move; a region that
would outgrow the screen simply stops admitting members, and their verdicts
append instead. Repainting is only sound while the region is the last thing
on screen, so it is guarded rather than trusted: the painter checks the
display's *epoch* — a count of every append-style output — and whatever
else printed commits the region on the spot. Off a terminal the whole
mechanism is off, and a turn simply appends: verdicts as they land, stream
text as it grows.

Block states map onto that: *open* blocks live in the region and may
update, a finished verdict *seals* its row, and the turn's rule *commits*
the region — printed history is never redrawn.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace as _line_replace
from typing import TYPE_CHECKING

from wcwidth import wcswidth

from . import lines as L
from .text import terminal_height, terminal_width, visible_width
from .theme import RESET

if TYPE_CHECKING:
    from .display import Display
    from .transcript import Block, Transcript
    from ..core.events import Event

#: Streamed fragments closer together than this are written as one — a render
#: request may be coalesced, never dropped: whatever is buffered is flushed
#: the moment anything else must land on the screen.
THROTTLE = 0.030

#: How many rows of a running tool's output the region carries — the tail,
#: not the whole log. What a call printed in full is the model's to read;
#: the reader gets the shape of it.
TOOL_TAIL_ROWS = 6

#: The thinking indicator, and how fast it turns.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_STEP = 0.080

_ANSI_SPLIT = re.compile(r"(\033\[[0-9;]*m)")


def fit_row(text: str, max_width: int) -> str:
    """Cut *text* down to *max_width* columns, keeping its escape codes.

    The one place the painter truncates instead of letting the terminal
    wrap: a row rewritten in place must stay exactly one terminal row tall,
    or every row offset in the region after it is wrong. Widths are
    measured, not counted, so CJK text is cut earlier than its character
    count suggests.
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


def wrap_rows(text: str, max_width: int) -> list[str]:
    """Hard-wrap *text* into visual rows — no ellipsis, because it continues.

    The partner of :func:`fit_row`: a row that is *allowed* to be tall (the
    unfinished line of a stream) is split at the width instead of cut, so
    what the model is still writing stays visible to the last character.
    """
    if max_width <= 0:
        return [text]
    rows: list[str] = []
    buf: list[str] = []
    width = 0
    for char in text:
        step = max(wcswidth(char), 0)
        if width + step > max_width:
            rows.append("".join(buf))
            buf = []
            width = 0
        buf.append(char)
        width += step
    rows.append("".join(buf))
    return rows or [""]


class Painter:
    """Projects the transcript's live region onto the terminal."""

    def __init__(self, display: "Display", *, animate: bool = False):
        self._d = display
        self._live = display.live
        #: Whether a background ticker may drive the spinner. The renderer
        #: enables it on a real terminal; a painter built directly (tests)
        #: stays frame-still unless someone calls :meth:`tick`.
        self._animate = animate
        #: ``True`` once a run's events say one is under way — the spinner
        #: is shown on that say-so, not inferred from silence.
        self._in_turn = False
        self._frame = 0
        self._ticker: "asyncio.Task | None" = None
        #: Whether the region shows anything a repaint must redraw while
        #: streaming (spinner frame, running-tool line) — what animates.
        self._spinning = False
        self._last: "Transcript | None" = None

        # ── the live region ─────────────────────────────────
        #: Rows of the region currently on screen, top to bottom — each
        #: exactly one terminal row. ``_frozen`` of them are final and are
        #: re-written with themselves only.
        self._span: list[str] = []
        self._frozen = 0
        self._h = 0
        #: The display epoch the region was last written at: any change is
        #: someone else's output, and the region is committed on the spot.
        self._span_epoch = 0
        #: Block ids this region has admitted (their rows are ours to
        #: rewrite) and ids it refused for want of room (their verdicts
        #: will append instead).
        self._admitted: set[str] = set()
        self._refused: set[str] = set()
        #: Whether anything live is in the region right now.
        self._has_live = False

        # ── the appended path's bookkeeping ─────────────────
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

        #: Whether the last paint was skipped for throttle and is still owed.
        self._paint_owed = False

    # ── The projection ────────────────────────────────────

    def paint(self, transcript: "Transcript", event: "Event | None" = None) -> None:
        """Draw what changed in the transcript since the last paint."""
        if not self._live:
            self._paint_appended(transcript)
            return

        from ..core.events import RunFailed, RunFinished, RunStarted

        if isinstance(event, RunStarted):
            self._in_turn = True
            self._ensure_ticker()
        terminal = isinstance(event, (RunFinished, RunFailed))
        if terminal:
            self._in_turn = False

        self._last = transcript
        if self._h and self._d.epoch != self._span_epoch:
            self._commit_span()  # someone else printed: our rows are history

        if terminal:
            # The turn closed: the region is committed as it stands, and the
            # rule (and anything never admitted) prints below it.
            self._commit_span()
            for block in self._tail(transcript):
                if block.kind == "tool" and block.id not in self._printed:
                    self._printed[block.id] = list(block.lines)
                    self._d.render_all(block.lines)
                else:
                    if self._append(block) and block.kind == "rule":
                        self._commit_span()
            return

        # A block that is not region material (streamed text, a notice, a
        # plugin message) appends — and appending ends the region: rows we
        # can no longer stand behind become history on the spot.
        if any(block.kind != "tool" for block in self._tail(transcript)):
            self._commit_span()
            self._paint_appended(transcript)
            return

        rows, live_start = self._project(transcript)
        self._repaint(rows, live_start)

    def redraw_all(self, transcript: "Transcript") -> None:
        """Reprint the whole document — the screen is stale, so start over.

        The history-replacement path: committed or not, every block is printed
        once, in order, and the live region — if any survived — is forgotten.
        """
        self._commit_span()
        self._flush_stream(close=True)
        self._printed.clear()
        self._in_turn = False
        self._d.clear_session()
        self._d.clear_screen()
        for block in transcript.blocks:
            for line in block.lines:
                self._d.render(line)
            self._printed[block.id] = list(block.lines)

    # ── The live region ───────────────────────────────────

    def _project(self, transcript: "Transcript") -> tuple[list[str], int]:
        """The region as rows, and where its rewritable part begins."""
        rows: list[str] = []
        first_live: int | None = None
        self._has_live = False
        cap = max(terminal_height() - 2, 1)
        for block in self._tail(transcript):
            member = self._member_rows(block, cap - len(rows))
            if member is None:
                continue
            if member.live and first_live is None:
                first_live = len(rows)
            if member.live:
                self._has_live = True
            rows.extend(member.rows)
        if first_live is None:
            first_live = len(rows)
        return rows, first_live

    def _tail(self, transcript: "Transcript") -> list["Block"]:
        """The blocks after the last rule — the current turn's material."""
        for i in range(len(transcript.blocks) - 1, -1, -1):
            if transcript.blocks[i].kind == "rule":
                return transcript.blocks[i + 1 :]
        return list(transcript.blocks)

    def _member_rows(self, block: "Block", room: int):
        """One block's contribution to the region, within *room* rows.

        Returns ``None`` when the block is not region material — it appends
        (and commits the region) instead. ``live`` rows may still change;
        the rest are final and only ride along under the live ones.
        """

        class _Member:
            __slots__ = ("rows", "live")

            def __init__(self, rows, live):
                self.rows = rows
                self.live = live

        if block.kind == "tool":
            if block.state == "running":
                if block.id in self._refused:
                    return None
                want = 1  # the summary line; the output tail joins in T3
                if block.id not in self._admitted:
                    if want > room:
                        self._refused.add(block.id)
                        return None
                    self._admitted.add(block.id)
                line = _line_replace(block.lines[0], text=self._running_text(block))
                return _Member([self._row(line)], True)
            if block.id in self._refused or block.id not in self._admitted:
                return None  # never ours: the appended path owns its landing
            # The verdict rides in the region one repaint — final rows under
            # whatever is still running — and is booked as printed, so no
            # appended path can land it twice.
            self._printed[block.id] = list(block.lines)
            return _Member([self._row(line) for line in block.lines], False)

        # Anything else that is not a region member ends the region: it
        # appends below rows we can no longer stand behind.
        if block.kind in ("notice", "user", "rule", "plugin") or (
            block.kind in ("answer", "reasoning")
        ):
            if block.state == "streaming":
                return None  # streams join the region in T2; until then they
                # append, and the epoch that follows commits the region.
            return None
        return None

    def _running_text(self, block: "Block") -> str:
        """The pending line as shown while the call runs."""
        return block.lines[0].text

    def _row(self, line: L.Line) -> str:
        """A line as one terminal row — fitted, never wrapping."""
        return fit_row(self._d.format(line), max(terminal_width() - 1, 1))

    def _repaint(self, rows: list[str], live_start: int) -> None:
        """Write *rows* as the region, replacing what is on screen.

        The whole mechanism in one shape: up to the top of the rows that may
        still move, rewrite row by row, then grow (the rest line gives way)
        or shed (surplus rows are deleted, pulling the rest line up). Rows
        above that top are final; they are rewritten with themselves only
        when the live rows below them move, which is what lets a long turn
        repaint cheaply. ``live_start`` is where the projection's live rows
        begin — final rows land there as they are written, never before.
        """
        if rows == self._span:
            self._frozen = max(self._frozen, min(live_start, len(rows)))
            return  # nothing changed on screen; a repaint would be noise
        top = self._frozen  # what is already final *on screen*
        h_old = self._h - top
        h_new = len(rows) - top
        if h_old <= 0 and h_new <= 0:
            self._span = rows
            self._h = len(rows)
            return
        buf: list[str] = []
        if h_old:
            buf.append(f"\x1b[{h_old}A")
        m = min(h_old, h_new)
        for i in range(m):
            buf.append("\r\x1b[K" + rows[top + i] + "\n")
        if h_new > h_old:
            for i in range(top + h_old, len(rows)):
                buf.append(rows[i] + "\n")
        elif h_new < h_old:
            buf.append("\x1b[M" * (h_old - h_new))
        if buf:
            self._d.write("".join(buf))
        if rows:
            self._span_epoch = self._d.epoch
        self._span = rows
        self._h = len(rows)
        self._frozen = max(top, min(live_start, len(rows)))

    def _commit_span(self) -> None:
        """Forget the rows: they belong to the scrollback now."""
        self._span = []
        self._frozen = 0
        self._h = 0
        self._admitted.clear()
        self._refused.clear()
        self._has_live = False

    # ── Animation ─────────────────────────────────────────

    def tick(self) -> None:
        """Advance the spinner one frame and repaint what it animates."""
        self._frame = (self._frame + 1) % len(SPINNER_FRAMES)
        if self._last is not None and self._h:
            rows, live = self._project(self._last)
            self._repaint(rows, live)

    def _ensure_ticker(self) -> None:
        """Start the background clock a spinning region needs, once."""
        if not (self._animate and self._live and self._in_turn):
            return
        if self._ticker is not None and not self._ticker.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ticker = loop.create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(SPINNER_STEP)
            if not (self._in_turn and self._has_live and self._h):
                return
            self.tick()

    # ── The appended path (a pipe, or a frozen region) ─────

    def _paint_appended(self, transcript: "Transcript") -> None:
        """Everything as appends — what a log can honour."""
        self._stream_step(transcript)
        for block in transcript.blocks:
            if block.kind == "tool":
                self._tool(block)
            elif block.kind in ("answer", "reasoning"):
                self._streamed_block(block)
            else:
                if self._append(block) and block.kind == "rule":
                    self._commit_span()

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

    def _tool(self, block: "Block") -> None:
        if block.id in self._printed or block.state == "running":
            return  # its verdict already landed — or there is none yet, and a
            # log keeps no placeholder rows
        self._d.render_all(block.lines)
        self._printed[block.id] = list(block.lines)

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
    "TOOL_TAIL_ROWS",
    "SPINNER_FRAMES",
    "SPINNER_STEP",
    "fit_row",
    "wrap_rows",
]
