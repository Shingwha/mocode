"""CLIRenderer — draws a conversation's event stream on a terminal.

A pure consumer. It subscribes to the conversation's channel and turns events
into lines from :mod:`mocode.cli.lines`; it implements no hooks and intercepts
nothing. What it owns is the state of a turn *as it is being drawn* — which tool
calls are in flight, and which row on screen each of them owns.

A tool call owns a row from the moment it starts: a dim placeholder that is
rewritten in place with its verdict when it ends. That gives a slow, quiet tool
a visible row while it runs at no cost in lines, and it keeps a parallel batch
to one row per call, in the order the calls were made rather than the order
they happen to finish.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.events import (
    IterationFinished,
    IterationStarted,
    Notice,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
)
from ..host.events import ConversationChanged
from . import lines as L

if TYPE_CHECKING:
    from ..core.events import Event
    from ..core.provider import Usage
    from ..host.conversation import Conversation
    from .display import Display


class CLIRenderer:
    """Draws a conversation: streamed text, the row of each tool call, the rule."""

    def __init__(self, display: "Display", conversation: "Conversation"):
        self._d = display
        self._conversation = conversation
        #: call_id -> args, for calls still in flight (their verdict needs them)
        self._running: dict[str, dict] = {}
        #: call_id -> the row its placeholder owns, when the terminal can redraw
        self._rows: dict[str, int | None] = {}

    def draw(self, event: "Event") -> None:
        match event:
            case TextDelta():
                self._d.stream(event.text, kind="answer")

            case ReasoningDelta():
                self._d.stream(event.text, kind="reasoning")

            case ToolCallStarted():
                self._tool_start(event)

            case ToolCallFinished():
                self._tool_done(event)

            case ToolOutput():
                # A running tool's output feeds the model, not the reader: what
                # a call did is worth a line, what it printed is not.
                pass

            case RunFinished():
                self._end_turn(usage=event.usage)

            case RunFailed():
                self._end_turn(error=f"{event.kind}: {event.error}")

            case ConversationChanged():
                # The history was replaced — resumed, cleared or imported — so
                # what is on screen is stale. The conversation is the source of
                # truth; this event is the instruction to re-read it.
                self._d.end_stream()
                self._d.conversation_changed(
                    self._conversation.messages, self._conversation.tools
                )
                self._clear()

            case Notice():
                self._d.render(L.notice(event.message, event.level))

            case RunStarted() | IterationStarted() | IterationFinished():
                pass  # a core event with nothing to draw

            case _:
                # Anything the renderer was not written for — including an
                # event type a plugin defined — describes itself through
                # ``summary()``, so it draws without any registration.
                self._d.render_event(event)

    # ── Tool calls ─────────────────────────────────────────

    def _tool_start(self, event: ToolCallStarted) -> None:
        """Claim the call's row, so its verdict has somewhere to land.

        Nothing else may be drawn while a batch runs, or the row offsets this
        row is addressed by stop being true — the display freezes the block the
        moment anything else is printed.
        """
        self._running[event.call_id] = event.args
        self._rows[event.call_id] = self._d.place(
            L.tool_pending(event.name, event.args, self._conversation.tools)
        )

    def _tool_done(self, event: ToolCallFinished) -> None:
        args = self._running.pop(event.call_id, {})
        row = self._rows.pop(event.call_id, None)
        self._d.rewrite(row, L.tool_close(event, args, self._conversation.tools))

    def _end_turn(self, *, usage: "Usage | None" = None, error: str = "") -> None:
        """Close the turn: what it cost (or why it died), then the rule."""
        self._d.end_stream()
        if error:
            self._d.error(error)
        elif usage and (usage.prompt_tokens or usage.completion_tokens):
            self._d.render(L.tokens(usage))
        self._d.render(L.divider())
        self._clear()

    def _clear(self) -> None:
        """Forget the batch. A turn that ended cleanly has nothing left in it."""
        self._running.clear()
        self._rows.clear()
