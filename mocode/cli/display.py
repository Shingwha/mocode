"""Display — terminal output primitives.

Deliberately thin. It knows how to put characters on a screen — styled lines,
incremental writes, the input prompt — and nothing about what a turn looks
like. That vocabulary lives in :mod:`mocode.cli.lines` as data, so the live
renderer and history replay cannot drift apart, and neither needs a terminal to
be tested. What turns a conversation's event stream into those lines is
:class:`~mocode.cli.render.CLIRenderer`.

Output is append-only except for one region: the rows a tool batch occupies
while it runs. Those are printed as dim placeholders and rewritten in place as
each call ends, which keeps a parallel batch to N fixed rows in call order.
Rewriting is safe only while the block is the last thing on screen, so it is
guarded rather than trusted — every line in the block is clamped to one terminal
row, and any other output freezes the block for good. Off a terminal the whole
mechanism is off, and a call simply appends its verdict when it finishes.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from wcwidth import wcswidth

from ..core.events import Event
from . import lines as L
from .text import terminal_height, terminal_width, visible_width
from .theme import DEFAULT_THEME, RESET, Theme

if TYPE_CHECKING:
    from .input import Input

#: The style a streamed block is written in. Neither answer nor reasoning
#: carries a marker — they are told apart by weight, which is the one signal a
#: stream can give without interrupting itself.
STREAMS: dict[str, str] = {"answer": "answer", "reasoning": "reasoning"}

_ANSI_SPLIT = re.compile(r"(\033\[[0-9;]*m)")


def fix_console() -> None:
    """Enable ANSI escape codes on Windows — the one platform quirk this has."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        pass


def clamp_visible(text: str, max_width: int) -> str:
    """Cut *text* down to *max_width* columns, keeping its escape codes.

    This is the one place the renderer truncates instead of letting the terminal
    wrap: a line rewritten in place has to stay exactly one row tall, or every
    row offset in the block after it is wrong. Widths are measured, not counted,
    so CJK text is cut earlier than its character count suggests.
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


class Display:
    """Terminal output — rendering only; input is delegated.

    Core primitives::

        Display.render(Line)              one styled line, from a lines.py builder
        Display.print(...)                raw output
        Display.stream(text, kind=)       incremental writes for streamed text
        Display.place(Line) -> row        a line a block may rewrite in place
        Display.rewrite(row, Line)        replace it, or append if that is no
                                          longer possible

    ``place`` / ``rewrite`` are how a tool batch stays N rows: the row a call
    claimed while running is the row its verdict lands on.
    """

    def __init__(
        self,
        input_: "Input",
        theme: Theme | None = None,
        live: bool | None = None,
    ):
        self._t = theme or DEFAULT_THEME
        self._input = input_
        self._stream_kind: str | None = None
        self._stream_line_start = True
        #: Whether rows can be rewritten. Off by default when output is
        #: redirected — escapes are noise in a log, and a log cannot be
        #: redrawn anyway.
        self._live = sys.stdout.isatty() if live is None else live
        #: Rows of the open block, top to bottom, still available for rewriting.
        self._block: list[int] = []
        fix_console()

    # ── Messages ───────────────────────────────────────────

    def info(self, text: str) -> None:
        self.render(L.notice(text, "info"))

    def warn(self, text: str) -> None:
        self.render(L.notice(text, "warn"))

    def error(self, text: str) -> None:
        self.render(L.notice(text, "error"))

    def conversation_changed(self, messages: list[dict], tools=None) -> None:
        """Redraw for a replaced conversation — resumed, imported or cleared."""
        self.clear_session()
        self.clear_screen()
        if messages:
            self.render_all(L.conversation(messages, tools))

    # ── Input delegation ───────────────────────────────────

    def clear_session(self) -> None:
        """Clear the paste store when starting a new conversation."""
        self._input.clear_session()

    async def prompt(self) -> str:
        return await self._input.prompt()

    # ── Output ─────────────────────────────────────────────

    def print(self, *args, **kwargs) -> None:
        """Raw print.

        Any pending streamed block is closed first — a line-oriented write
        always starts on its own line — and any tool block still open is frozen,
        because what this prints is not another verdict landing in it.
        """
        self._invalidate()
        self._emit(*args, **kwargs)

    def _emit(self, *args, **kwargs) -> None:
        """Print one line without touching the block — for the block's own rows."""
        self.end_stream()
        print(*args, **kwargs)
        self._stream_line_start = True

    def render(self, line: L.Line) -> None:
        """Put one styled line on screen — the core output primitive."""
        self.print(self.format(line))

    def render_all(self, lines: list[L.Line]) -> None:
        for line in lines:
            self.render(line)

    def format(self, line: L.Line) -> str:
        """A :class:`Line` as the string to print. Pure, so it can be asserted on."""
        t = self._t
        parts = []
        if line.icon:
            parts.append(self._colored(line.icon, line.icon_style or line.style))
        if line.text:
            parts.append(self._colored(line.text, line.style))
        head = " ".join(parts)
        if not line.note:
            return head

        note = f"{t.muted}{line.note}{t.reset}"
        if not head:
            return note
        # The dot separates the note from *text*; an icon on its own is not text,
        # so a verdict that carries the whole line reads "✓ 14 files", not
        # "✓ · 14 files".
        return head + (" · " if line.text else " ") + note

    def _colored(self, text: str, style: str) -> str:
        code = getattr(self._t, style, "")
        return f"{code}{text}{self._t.reset}" if code else text

    # ── The live block ─────────────────────────────────────

    def place(self, line: L.Line) -> int | None:
        """Print a line the block may rewrite later, and return its row handle.

        Returns ``None`` without printing when the terminal cannot redraw or the
        block already fills the screen — the caller then has nothing on screen to
        replace, and draws the line when it is finished instead.
        """
        if not self._live or len(self._block) >= terminal_height() - 1:
            return None
        self._emit(self._fit(self.format(line)))
        self._block.append(len(self._block))
        return self._block[-1]

    def rewrite(self, row: int | None, line: L.Line) -> None:
        """Replace the line at *row*, or append it if that row is gone.

        Rewriting moves up to the row, clears it, writes the new line and
        returns to the bottom of the block — the cursor never leaves the block,
        which is what makes the next rewrite's offsets still correct.
        """
        if row is None or not self._live or row >= len(self._block):
            self.render(line)
            return

        offset = len(self._block) - row
        text = self._fit(self.format(line))
        sys.stdout.write(f"\033[{offset}A\033[K{text}\033[{offset}B\r")
        sys.stdout.flush()
        self._stream_kind = None
        self._stream_line_start = True

    def _invalidate(self) -> None:
        """Freeze the block: its rows are no longer ours to rewrite.

        Called before anything is printed that is not a verdict, because a line
        this layer did not measure may wrap, and a wrapped line shifts every row
        offset below it.
        """
        self._block.clear()

    def _fit(self, text: str) -> str:
        """Clamp to one terminal row — see :func:`clamp_visible`."""
        return clamp_visible(text, max(terminal_width() - 1, 1))

    # ── Streamed text ──────────────────────────────────────

    def write(self, text: str) -> None:
        """Append a fragment to the current line without disturbing it."""
        print(text, end="", flush=True)

    def user_message(self, text: str) -> None:
        """Echo what you typed, and the blank line that separates it from the reply."""
        self.render_all(L.prompt(text))

    def stream(self, text: str, *, kind: str = "answer") -> None:
        """Append streamed text, opening a block of *kind* if none is open.

        Nothing is written in front of a streamed line: a block is opened only
        so that the end of it can be found, and to pick the style.
        """
        self._invalidate()
        if self._stream_kind != kind:
            self.end_stream()
            self._stream_kind = kind
            self._stream_line_start = True

        style = STREAMS.get(kind, kind)
        for i, chunk in enumerate(text.split("\n")):
            if i:
                print(flush=True)  # the newline that split this chunk
                self._stream_line_start = True
            if not chunk:
                continue
            self._stream_line_start = False
            self.write(self._colored(chunk, style))

    def end_stream(self) -> None:
        """Close an open streamed block, so the next write starts on a new line."""
        if self._stream_kind is None:
            return
        if not self._stream_line_start:
            print(flush=True)
        self._stream_kind = None
        self._stream_line_start = True

    # ── Plugin events ──────────────────────────────────────

    def render_event(self, event: object) -> None:
        """Show an event that has no dedicated rendering — one line, from the event.

        The event describes itself (``Event.summary``), so a plugin's own event
        type needs no registration and works in every frontend.
        """
        summary = event.summary() if isinstance(event, Event) else str(event)
        self.render(L.notice(summary, "info"))

    # ── Screen ─────────────────────────────────────────────

    def clear_screen(self) -> None:
        """Wipe the screen and home the cursor — an escape, not a subprocess."""
        self._invalidate()
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
