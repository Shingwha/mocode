"""Markdown, the restrained way — fences while a block streams, rich at seal.

Two halves, deliberately not one parser:

* **Streaming** weighs every repaint against the block it redraws, so the
  stream allows itself the cheapest of marks only: fenced code blocks,
  detected line by line as lines complete. No headings, no lists, no inline
  markup — a streamed line is committed the moment it finishes, and the
  judgment of what it is can only use what was complete at that moment. The
  row that opens a fence passes plain (the fence did not exist yet); the rows
  inside are de-emphasised; the row that closes it reads as the frame's end.
* **Settled** blocks may be fully rendered through ``rich`` — the optional
  ``tui`` dependency group (``uv sync --group tui``). Without it the sealed
  block keeps its plain lines, and a redirected display never asks: a pipe
  keeps its byte-for-byte plain appends either way.

Nothing here touches a terminal: the tracker is fed strings, the styler
returns :class:`~mocode.cli.lines.Line` data, and the rich half renders to an
ANSI string a :class:`~mocode.cli.display.Display` can print like any other
line.
"""

from __future__ import annotations

from . import lines as L
from .text import terminal_width
from .theme import Theme

try:  # the optional ``tui`` group — absent is the default install
    import rich.console
    import rich.markdown
except ImportError:  # pragma: no cover - depends on the environment
    rich = None

#: The marker of a fenced code block. Tilde fences stay plain — backtick
#: fences are the shape a model actually streams.
FENCE = "```"


class FenceTracker:
    """Where fenced code blocks stand, learned one completed line at a time.

    Feed every completed line in order; :meth:`in_fence` answers for the line
    about to be committed — *before* feeding the line that may change the
    state. There is no nesting: a fence marker inside a fence closes it,
    because a streamed document is trusted over a recovered one.
    """

    def __init__(self) -> None:
        self._in_fence = False

    def feed(self, line: str) -> None:
        """Update the fence state with one completed line (a fence row too)."""
        if line.lstrip().startswith(FENCE):
            self._in_fence = not self._in_fence

    def in_fence(self) -> bool:
        """Whether the next completed line lands inside a fenced code block."""
        return self._in_fence


def style_code_line(line: str, theme: Theme) -> L.Line:
    """A line inside a fenced code block, as the stream commits it.

    De-emphasised against the prose around it — the one mark streaming
    allows itself. A theme without a ``dim`` style falls back to the plain
    answer style rather than inventing a colour.
    """
    return L.Line(text=line, style="dim" if theme.dim else "answer")


def render_settled(text: str, kind: str) -> list[L.Line] | None:
    """A sealed answer or reasoning block, fully rendered through ``rich``.

    Returns ``None`` when rich is not importable — the caller then keeps the
    plain lines it would have drawn anyway. The render is one snapshot at the
    terminal's width: the rows carry their own ANSI, so they are submitted
    with an empty style and printed verbatim.
    """
    if rich is None:
        return None
    import rich.console

    console = rich.console.Console(force_terminal=True, width=terminal_width())
    with console.capture() as capture:
        console.print(rich.markdown.Markdown(text))
    rows = capture.get().rstrip("\n").splitlines()
    return [L.Line(text=row) for row in rows]


__all__ = [
    "FENCE",
    "FenceTracker",
    "render_settled",
    "style_code_line",
]
