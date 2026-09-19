"""Host events — what the conversation itself announces, outside any run.

A run's events say what the model and its tools are doing. These say what
happened to the conversation: it was replaced, so whatever is on screen is out
of date. A frontend renders them the same way it renders everything else —
subscribed to the conversation's channel, with no private protocol to satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..core.events import Event


@dataclass
class ConversationChanged(Event):
    """The conversation was replaced — resumed, imported, or started over.

    The event carries no payload: the conversation is the source of truth, and a
    reader that wants the messages reads them (``conversation.messages``). It is
    a redraw instruction, and it is published to every subscriber so that all of
    them — a terminal, a web tab left open elsewhere — agree on what is current.
    """

    type: ClassVar[str] = "conversation_changed"

    def summary(self) -> str:
        return "conversation replaced"


__all__ = ["ConversationChanged"]
