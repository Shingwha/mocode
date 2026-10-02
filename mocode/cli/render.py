"""CLIRenderer — a conversation's event stream, drawn.

A pure consumer, thinner than it looks: it subscribes to the conversation's
channel, folds each event into the
:class:`~mocode.cli.transcript.Transcript` — the document model — and lets the
:class:`~mocode.cli.painter.Painter` project what changed onto the terminal.
It implements no hooks and intercepts nothing; the shapes on screen are the
transcript's, and the only state it owns is the conversation it reads history
from.

The one event it treats specially is the host saying the history was replaced:
that is a redraw instruction, and the conversation — not the stream — is where
the new history lives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..host.events import ConversationChanged
from .markdown import render_settled
from .painter import Painter
from .transcript import Transcript

if TYPE_CHECKING:
    from ..core.events import Event
    from ..host.conversation import Conversation
    from .display import Display


class CLIRenderer:
    """Draws a conversation: fold each event, then paint what changed."""

    def __init__(self, display: "Display", conversation: "Conversation", drawers=None):
        self._d = display
        self._conversation = conversation
        # A redirected display never asks for the full render: a pipe keeps
        # its plain appends, rich installed or not.
        self._transcript = Transcript(
            conversation.tools,
            drawers=drawers,
            markdown=render_settled if display.live else None,
        )
        self._painter = Painter(display, animate=display.live)

    def draw(self, event: "Event") -> None:
        if isinstance(event, ConversationChanged):
            # The history was replaced — resumed, cleared or imported — so
            # what is on screen is stale. The conversation is the source of
            # truth; this event is the instruction to re-read it.
            self._transcript.apply(event)
            self._transcript.apply_history(
                self._conversation.messages, self._conversation.tools
            )
            self._painter.redraw_all(self._transcript)
            return
        self._transcript.apply(event)
        self._painter.paint(self._transcript, event)
