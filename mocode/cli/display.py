"""Display — terminal output primitives, and the Frontend a plugin sees.

Deliberately thin. It knows how to put characters on a screen — styled lines,
incremental writes, the input prompt — and nothing about what a turn looks
like. That vocabulary lives in :mod:`mocode.cli.lines` as data, so the live
renderer and history replay cannot drift apart, and neither needs a terminal to
be tested.

It is also the terminal's implementation of
:class:`~mocode.host.frontend.Frontend` — the four methods a plugin may call
without knowing a terminal exists.

Output is append-only: the only state is which streamed block is open, and it
exists solely to know where a line starts. Nothing redraws a line that has
already been written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.events import Event
from . import lines as L
from .theme import DEFAULT_THEME, Theme

if TYPE_CHECKING:
    from .input import Input

#: The style a streamed block is written in. Neither answer nor reasoning
#: carries a marker — they are told apart by weight, which is the one signal a
#: stream can give without interrupting itself.
STREAMS: dict[str, str] = {"answer": "answer", "reasoning": "reasoning"}


class Display:
    """Terminal output — rendering only; input is delegated.

    Core primitives::

        Display.render(Line)          one styled line, from a lines.py builder
        Display.print(...)            raw output
        Display.stream(text, kind=)   incremental writes for streamed text
    """

    def __init__(self, input_: "Input", theme: Theme | None = None):
        self._t = theme or DEFAULT_THEME
        self._input = input_
        self._stream_kind: str | None = None
        self._stream_line_start = True

    # ── Frontend ───────────────────────────────────────────

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
        always starts on its own line.
        """
        self.end_stream()
        print(*args, **kwargs)

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
        import os

        os.system("cls" if os.name == "nt" else "clear")
