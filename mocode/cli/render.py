"""CLIRenderer — draws a conversation's event stream on a terminal.

A pure consumer. It subscribes to the conversation's channel and turns events
into lines from :mod:`mocode.cli.lines`; it implements no hooks and intercepts
nothing. What it owns is the state of a turn *as it is being drawn* — which tool
calls are in flight, and which row on screen each of them owns.

A tool call owns a row from the moment the model names it: a dim placeholder
opened while the arguments stream, rewritten in place with the final
arguments when the call starts and with its verdict when it ends. That gives
a slow, quiet tool a visible row while it runs at no cost in lines, and it
keeps a parallel batch to one row per call, in the order the calls were made
rather than the order they happen to finish.
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
    ToolCallArgsDelta,
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
        """Draw one event — the renderer's whole subscription surface.

        A ``match`` over the events a terminal can represent, with an
        intentional fallback: anything it was not written for — a core event
        with nothing to draw, an event type a plugin defined — either describes
        itself through ``summary()`` or draws nothing. That is why a plugin's
        new event needs no change here, and why the host needs no renderer
        registry: an event renders itself.
        """
        match event:
            case TextDelta():
                self._d.stream(event.text, kind="answer")

            case ReasoningDelta():
                self._d.stream(event.text, kind="reasoning")

            case ToolCallArgsDelta():
                self._tool_forming(event)

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

    def _tool_forming(self, event: ToolCallArgsDelta) -> None:
        """Open the call's row the moment the model names the tool.

        Arguments stream before the call runs, and the first fragment carries
        the name — so the row opens on the tool, and the name-only pending
        line stands there while the rest of the arguments arrive. Later
        fragments draw nothing: partial JSON has no summary to show, and a row
        rewritten per fragment is a row that flickers. ``ToolCallStarted``
        rewrites the row with the final arguments.
        """
        if event.call_id in self._rows or not event.name:
            return
        self._rows[event.call_id] = self._d.place(
            L.tool_pending(event.name, {}, self._conversation.tools)
        )

    def _tool_start(self, event: ToolCallStarted) -> None:
        """Give the call's row the final arguments, so its verdict has somewhere
        to land.

        A row the argument stream already opened is rewritten in place with the
        final arguments; a call no fragment announced — a program-origin call,
        a model that never named the tool — claims its row now. Nothing else
        may be drawn while a batch runs, or the row offsets this row is
        addressed by stop being true — the display freezes the block the moment
        anything else is printed.
        """
        row = self._rows.get(event.call_id)
        line = L.tool_pending(event.name, event.args, self._conversation.tools)
        if row is None:
            self._rows[event.call_id] = self._d.place(line)
        else:
            self._d.rewrite(row, line)
        self._running[event.call_id] = event.args

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
