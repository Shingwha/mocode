"""Painter — the transcript, as it lands on a terminal.

The only component that knows a screen can be *redrawn*. Everything the
transcript has already committed is printed and forgotten — the terminal's
scrollback is the archive, not us. What stays rewritable is the **live
region**: the rows the current turn's open blocks occupy — a running tool
with its output tail, a streaming answer's last unfinished line, the
thinking spinner while nothing else is on stage. Any of them changing is
one repaint of the region: move up to its top, rewrite row by row, add the
rows it grew or delete the rows it shed.

A stream paints by that rule and one more: the moment a streamed line is
finished it is appended and belongs to the scrollback — only the line still
being written is a member of the region, rewritten in place, counted in
visual rows because it wraps. A tool's verdict, once landed, is likewise
final: it rides in the region under whatever is still running, rewritten
with itself, and commits with the turn.

Rows are addressed in *visual* lines — a row that wraps is split before it
enters the region, so an offset of one corrupts nothing. A region that
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
from .markdown import FenceTracker, style_code_line
from .text import terminal_height, terminal_width, visible_width
from .theme import RESET
from .transcript import RUNNING, STREAMING

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


def _stream_lines(text: str, kind: str) -> list[L.Line]:
    """A streamed text as the lines the same text commits as.

    One vocabulary for both halves of a stream: the row the region rewrites
    while a line is unfinished, and the lines that append once it is done.
    """
    return L.reasoning(text) if kind == "reasoning" else L.answer(text)


class Painter:
    """Projects the transcript's live region onto the terminal."""

    def __init__(self, display: "Display", *, animate: bool = False):
        self._d = display
        self._live = display.live
        #: Whether a landed call keeps its output tail — the verbose view.
        # Off by default: a landed call is one line, and what it printed in
        # full is the model's to read. (Key binding is the input layer's.)
        self.verbose = False
        #: Whether a background ticker may drive the spinner. The renderer
        # enables it on a real terminal; a painter built directly (tests)
        # stays frame-still unless someone calls :meth:`tick`.
        self._animate = animate
        #: ``True`` once a run's events say one is under way — the spinner
        # is shown on that say-so, not inferred from silence.
        self._in_turn = False
        self._frame = 0
        self._ticker: "asyncio.Task | None" = None
        #: Whether the region shows anything a repaint must redraw while
        # streaming (spinner frame, running-tool line) — what animates. The
        #: ticker ticks on that alone; a stream's rows are redrawn by its
        #: own flushes.
        self._spinning = False
        self._last: "Transcript | None" = None

        # ── the live region ─────────────────────────────────
        #: Rows of the region currently on screen, top to bottom — each
        #: exactly one terminal row. ``_frozen`` of them are final and are
        #: re-written with themselves only.
        self._span: list[str] = []
        self._frozen = 0
        self._h = 0
        #: Whether the cursor sits at the end of the region's last row — a
        # line still being written — rather than on the line below it.
        self._open_line = False
        #: The display epoch the region was last written at: any change is
        #: someone else's output, and the region is committed on the spot.
        self._span_epoch = 0
        #: Block ids this region has admitted (their rows are ours to
        #: rewrite) and ids it refused for want of room (their verdicts
        # will append instead).
        self._admitted: set[str] = set()
        self._refused: set[str] = set()
        #: Whether anything live is in the region right now.
        self._has_live = False
        #: The rows of the *sealed* stream blocks whose last line still
        #: rides in the region — recorded at close, so a later re-render of
        #: the block's lines (a full markdown render, say) never rewrites
        #: what the stream actually wrote.
        self._block_rows: dict[int, list[str]] = {}
        #: Whether the last projection could not seat the stream's line,
        #: and whether its rows are the region's last — an open line.
        self._declined = False
        self._open_tail = False

        # ── the appended path's bookkeeping ─────────────────
        #: block id -> the lines of it that are already on screen (a streamed
        #: block's lines are accounted for the moment it opens — they reach
        # the screen as text, never as lines).
        self._printed: dict[str, list] = {}
        # The stream in flight: which block, how much of its text is written,
        # and what is buffered waiting for the coalescing window to close.
        # ``_stream_partial`` is the unfinished line — the region's rows for
        # it, left unterminated so the next fragment continues it.
        self._stream_id: str | None = None
        self._stream_kind = ""
        self._streamed = 0
        self._stream_partial = ""
        self._pending = ""
        self._last_flush: float | None = None
        #: Fence state per stream kind — each block starts outside a fence,
        #: so a tracker resets the moment a block of that kind opens.
        self._fences: dict[str, FenceTracker] = {}

    # ── The projection ────────────────────────────────────

    def paint(self, transcript: "Transcript", event: "Event | None" = None) -> None:
        """Draw what changed in the transcript since the last paint."""
        if not self._live:
            self._paint_appended(transcript)
            return

        from ..core.events import (
            ReasoningDelta,
            RunFailed,
            RunFinished,
            RunStarted,
            TextDelta,
        )

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
            # The turn closed: what the stream still holds is written, the
            # region is painted one last time — the spinner is gone, the
            # stream's line is final — and everything commits, with the rule
            # and any verdict never admitted appended below it.
            self._stream_flush(transcript, force=True)
            rows, live_start = self._project(transcript)
            self._repaint(rows, live_start)
            self._commit_span()
            self._append_turn(transcript)
            return

        # The stream first: whatever else is about to land, the text it
        # holds goes down before it. A delta that only extends a live stream
        # may wait out the coalescing window — nothing else moved, so the
        # paint can stop; the next event writes it.
        stream_event = isinstance(event, (TextDelta, ReasoningDelta))
        if not self._stream_flush(transcript, force=not stream_event):
            if stream_event:
                return

        rows, live_start = self._project(transcript)
        if self._declined or self._to_append(transcript):
            # Nothing here may be rewritten where it stands: a notice, a
            # prompt, a rule — or a stream whose line no longer fits. The
            # region's rows become history and the blocks append.
            self._commit_span()
            self._paint_appended(transcript)
            return
        self._repaint(rows, live_start, open_line=self._open_tail)

    def redraw_all(self, transcript: "Transcript") -> None:
        """Reprint the whole document — the screen is stale, so start over.

        The history-replacement path: committed or not, every block is printed
        once, in order, and the live region — if any survived — is forgotten.
        """
        self._commit_span()
        self._reset_stream()
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
        self._declined = False
        self._open_tail = False
        self._spinning = False
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
        # Nothing is on stage and the turn is under way: the thinking row,
        # first live row of the region and its top row when nothing else
        # has landed yet. A full region makes no room for it.
        if self._in_turn and not self._has_live and len(rows) < cap:
            rows.append(self._row(L.thinking(SPINNER_FRAMES[self._frame])))
            self._has_live = True
            self._spinning = True
            if first_live is None:
                first_live = len(rows) - 1
        if first_live is None:
            first_live = len(rows)
        return rows, first_live

    def _tail(self, transcript: "Transcript") -> list["Block"]:
        """The blocks after the last rule — the current turn's material."""
        for i in range(len(transcript.blocks) - 1, -1, -1):
            if transcript.blocks[i].kind == "rule":
                return transcript.blocks[i + 1 :]
        return list(transcript.blocks)

    def _turn_blocks(self, transcript: "Transcript") -> list["Block"]:
        """The blocks of the turn that just closed — its rule included."""
        rules = [i for i, b in enumerate(transcript.blocks) if b.kind == "rule"]
        start = rules[-2] + 1 if len(rules) >= 2 else 0
        return transcript.blocks[start:]

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
            # The ledger is keyed by the block object, not its id: a call id
            # is the model's to reuse, and two calls sharing one would
            # otherwise share a row.
            who = id(block)
            if block.state == RUNNING:
                if who in self._refused:
                    return None
                tail = self._tail_rows(block)
                want = 1 + len(tail)  # the summary line, and what it printed
                if who not in self._admitted:
                    if want > room:
                        self._refused.add(who)
                        return None
                    self._admitted.add(who)
                line = _line_replace(block.lines[0], text=self._running_text(block))
                self._spinning = True  # the frame moves while the call runs
                return _Member([self._row(line)] + tail, True)
            if who in self._refused or who not in self._admitted:
                return None  # never ours: the appended path owns its landing
            # The verdict rides in the region one repaint — final rows under
            # whatever is still running — and is booked as printed, so no
            # appended path can land it twice. The tail it grows over goes
            # with it: a landed call shows its output tail only in verbose,
            # where what it printed is worth a second look.
            self._printed[block.id] = list(block.lines)
            rows = [self._row(line) for line in block.lines]
            if self.verbose:
                rows += self._tail_rows(block)
            return _Member(rows, False)

        if block.kind in ("answer", "reasoning"):
            if block.state == STREAMING:
                # The unfinished line, and nothing else of the block: the
                # lines before it are already appended.
                if block.id != self._stream_id:
                    return None
                rows = self._stream_row_list(self._stream_partial, block.kind)
                if not rows:
                    return None
                if len(rows) > room:
                    # No room to rewrite it where it stands: from here the
                    # line joins the appended stream.
                    self._declined = True
                    return None
                self._open_tail = True  # the last row is still being written
                return _Member(rows, True)
            # Sealed: its last line's rows ride in the region as final rows
            # — the rows the stream was rewritten with, and no others.
            rows = self._block_rows.get(id(block))
            if not rows or not block.lines:
                return None
            return _Member(rows, False)

        # Anything else that is not a region member ends the region: it
        # appends below rows we can no longer stand behind.
        return None

    def _to_append(self, transcript: "Transcript") -> bool:
        """Whether the tail holds a block that must append below the region.

        A block the region owns is fine wherever it sits; anything else —
        a notice, a prompt, a rule — has to be written now, and writing it
        ends the region. A verdict the region refused is not such a block:
        it appends when the turn's text or its end asks for it.
        """
        for block in self._tail(transcript):
            if block.kind in ("tool", "answer", "reasoning"):
                continue
            if block.id not in self._printed:
                return True
        return False

    def _running_text(self, block: "Block") -> str:
        """The pending line as shown while the call runs.

        The marker that says "still going" is the spinner's current frame,
        not a static ellipsis — the line moves because the call is moving.
        """
        text = block.lines[0].text
        if text.endswith("…"):  # the pending ellipsis, replaced by the frame
            text = text[:-1]
        return f"{text} {SPINNER_FRAMES[self._frame]}"

    def _tail_rows(self, block: "Block") -> list[str]:
        """The last rows of what a running call printed — the tail, not the log.

        Output streams in faster than a repaint is allowed to, so the newest
        lines replace the oldest: the region carries ``TOOL_TAIL_ROWS`` rows
        of it, wrapped to the width, and each arrival is one repaint rather
        than one append. What a call printed in full reaches the model, not
        the reader.
        """
        text = block.meta.get("output", "")
        if not text:
            return []
        rows: list[str] = []
        for line in text.splitlines()[-TOOL_TAIL_ROWS * 4 :]:
            rows.extend(self._stream_rows_of_line(L.Line(text=line)))
        return rows[-TOOL_TAIL_ROWS:]

    def _width(self) -> int:
        return max(terminal_width(), 1)

    def _row(self, line: L.Line) -> str:
        """A line as one terminal row — fitted, never wrapping."""
        return fit_row(self._d.format(line), max(terminal_width() - 1, 1))

    def _stream_row_list(self, text: str, kind: str) -> list[str]:
        """A streamed text as the rows it occupies — wrapped, never cut."""
        rows: list[str] = []
        for line in _stream_lines(text, kind):
            rows.extend(self._stream_rows_of_line(line))
        return rows

    def _stream_rows_of_line(self, line: L.Line) -> list[str]:
        return wrap_rows(self._d.format(line), self._width())

    def _repaint(
        self, rows: list[str], live_start: int, *, open_line: bool = False
    ) -> None:
        """Write *rows* as the region, replacing what is on screen.

        The whole mechanism in one shape: up to the top of the rows that may
        still move, rewrite row by row, then grow (the rest line gives way)
        or shed (surplus rows are deleted, pulling the rest line up). Rows
        above that top are final; they are rewritten with themselves only
        when the live rows below them move, which is what lets a long turn
        repaint cheaply. ``live_start`` is where the projection's live rows
        begin — final rows land there as they are written, never before.

        ``open_line`` says the region's last row is a line still being
        written: it is left unterminated, so the cursor stays at its end
        where the stream continues, instead of dropping to the line below.
        """
        if rows == self._span and open_line == self._open_line:
            self._frozen = max(self._frozen, min(live_start, len(rows)))
            return  # nothing changed on screen; a repaint would be noise
        top = self._frozen  # what is already final *on screen*
        h_old = self._h - top
        h_new = len(rows) - top
        if h_old <= 0 and h_new <= 0:
            self._span = rows
            self._h = len(rows)
            self._open_line = open_line
            return
        buf: list[str] = []
        if h_old:
            buf.append(f"\x1b[{h_old}A")
        m = min(h_old, h_new)
        for i in range(m):
            buf.append("\r\x1b[K" + rows[top + i])
            if open_line and i == m - 1 and h_new <= h_old:
                continue  # the cursor stays where the stream continues
            buf.append("\n")
        if h_new > h_old:
            for i in range(top + h_old, len(rows)):
                buf.append(rows[i])
                buf.append("" if open_line and i == len(rows) - 1 else "\n")
        elif h_new < h_old:
            buf.append("\x1b[M" * (h_old - h_new))
        if buf:
            self._d.write("".join(buf))
        if rows:
            self._span_epoch = self._d.epoch
        self._span = rows
        self._h = len(rows)
        self._frozen = max(top, min(live_start, len(rows)))
        self._open_line = open_line

    def _commit_span(self) -> None:
        """Forget the rows: they belong to the scrollback now."""
        self._span = []
        self._frozen = 0
        self._h = 0
        self._open_line = False
        self._admitted.clear()
        self._refused.clear()
        self._has_live = False
        self._block_rows.clear()

    # ── The stream ────────────────────────────────────────

    def _stream_flush(self, transcript: "Transcript", *, force: bool) -> bool:
        """Write what the stream holds that the screen does not.

        Returns whether anything was written — a delta that only extends a
        live stream may wait out the coalescing window, and then nothing
        else moved, so the paint can stop. Whatever *ends* a stream is
        written regardless: a line is not lost to a window.
        """
        blocks = transcript.blocks
        active = blocks[-1] if blocks and blocks[-1].state == STREAMING else None
        wrote = False
        # Whatever the tracked stream holds is written before anything
        # replaces it — it is ending, and an ending stream does not wait.
        mine = self._find(blocks, self._stream_id)
        ending = mine is None or mine.state != STREAMING
        if mine is not None and self._write_due(mine, force or ending):
            self._write_stream(mine.meta["text"])
            wrote = True
        if ending:
            self._close_stream(mine)
        if active is not None and active.id != self._stream_id:
            self._begin_stream(active)
            if self._write_due(active, force):
                self._write_stream(active.meta["text"])
                wrote = True
        return wrote

    @staticmethod
    def _find(blocks: list["Block"], block_id: str | None) -> "Block | None":
        if block_id is None:
            return None
        for block in reversed(blocks):
            if block.id == block_id:
                return block
        return None

    def _write_due(self, block: "Block", force: bool) -> bool:
        """Whether the stream's new text is written now or waits a window.

        A window that has not closed yet coalesces; a stream that has ended
        — sealed, or replaced by another — does not wait, because its line
        is about to be closed and the text would be lost to it.
        """
        if self._streamed >= len(block.meta.get("text", "")):
            return False
        return force or block.state != STREAMING or not self._throttled()

    def _throttled(self) -> bool:
        return (
            self._last_flush is not None
            and time.monotonic() - self._last_flush < THROTTLE
        )

    def _begin_stream(self, block: "Block") -> None:
        self._stream_id = block.id
        self._stream_kind = block.kind
        self._streamed = 0
        self._stream_partial = ""
        self._fences[block.kind] = FenceTracker()

    def _reset_stream(self) -> None:
        """Forget the stream entirely — a redraw starts the document over."""
        self._flush_stream(close=True)
        self._stream_id = None
        self._stream_kind = ""
        self._streamed = 0
        self._stream_partial = ""
        self._block_rows.clear()
        self._fences.clear()
        self._pending = ""
        self._last_flush = None

    def _close_stream(self, block: "Block | None") -> None:
        """End the stream's line: it is final now and rides in the region.

        The unfinished line's rows are the region's last rows — whether the
        stream was mid-flight (they are already there) or its line just
        completed a flush that appended everything before it — so ending the
        line rewrites them where they are, prints the newline that finishes
        them, and freezes them: final rows, like a landed verdict, riding in
        the region until the turn commits. The lines before it are appended
        already, which makes the whole of the block's lines on screen, and
        it is booked as such.
        """
        if self._stream_id is None:
            return
        if self._stream_partial:
            keep = self._stream_row_list(self._stream_partial, self._stream_kind)
            head = self._span[: self._frozen]
            self._repaint(head + keep, len(head), open_line=True)
            self._d.print()  # the newline that finishes the line
            if block is not None:
                self._block_rows[id(block)] = keep
            self._frozen = len(self._span)  # everything above is final too
            self._open_line = False
            self._span_epoch = self._d.epoch
        if block is not None and block.lines:
            self._printed[self._stream_id] = list(block.lines)
        self._stream_id = None
        self._stream_kind = ""
        self._streamed = 0
        self._stream_partial = ""

    def _write_stream(self, text: str) -> None:
        """Write what is new in the stream.

        A line that is *finished* appends — it is committed, never rewritten
        again — and takes the region's rows above it with it into the
        scrollback: the region has to stay the last thing on screen, and the
        finished line pushes the cursor below whatever it carried. The
        unfinished line is then the region's rows, rewritten in place by the
        next flush.
        """
        new = text[self._streamed :]
        self._streamed = len(text)
        self._last_flush = time.monotonic()
        shown = self._stream_partial  # what is on screen already
        done, partial = "", self._stream_partial + new
        if "\n" in partial:
            done, partial = partial.rsplit("\n", 1)
        if done:
            fresh = done[len(shown) :]
            if fresh:
                self._d.render_all(self._streamed_lines(fresh + "\n", self._stream_kind))
            else:
                self._d.print()  # the line was already written: end it
            # The finished lines commit, and with them the rows they sat
            # under — the region starts again below them, at the new line.
            self._commit_span()
        self._stream_partial = partial

    def _streamed_lines(self, text: str, kind: str) -> list[L.Line]:
        """Completed streamed lines as they append — fenced ones de-emphasised.

        *text* is newline-terminated: the terminator closes the last of the
        completed lines, empty ones included. The fence judgment belongs to
        the moment a line completes: the row that opens a fence passes plain
        (the fence did not exist yet), the rows inside it dim, and its
        closing row reads as the frame's end.
        """
        tracker = self._fences.setdefault(kind, FenceTracker())
        style = "reasoning" if kind == "reasoning" else "answer"
        out: list[L.Line] = []
        for row in text.splitlines():
            out.append(
                style_code_line(row, self._d.theme)
                if tracker.in_fence()
                else L.Line(text=row, style=style)
            )
            tracker.feed(row)
        return out

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
        """The clock the spinner turns on — one per turn, nothing more."""
        while True:
            await asyncio.sleep(SPINNER_STEP)
            if not self._in_turn:
                return  # the turn closed: the spinner row goes with it
            if self._spinning:
                self.tick()

    # ── The appended path (a pipe, or a frozen region) ─────

    def _append_turn(self, transcript: "Transcript") -> None:
        """The turn's closing blocks: its rule, and verdicts never admitted.

        A landed call the region refused for want of room prints here, at
        the turn's end, rather than not at all; a stream's lines are on the
        screen already, appended as they were written or ridden in the
        region, so they are not printed twice.
        """
        for block in self._turn_blocks(transcript):
            if block.kind == "tool":
                if block.id not in self._printed and block.state != RUNNING:
                    self._printed[block.id] = list(block.lines)
                    self._d.render_all(block.lines)
            elif block.kind not in ("answer", "reasoning"):
                self._append(block)

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
            fresh = block.lines[len(printed) :]  # a follow-up: only what is new
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
