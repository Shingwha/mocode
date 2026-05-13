"""Channel message DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InboundMessage:
    """Message from a platform channel."""

    channel: str
    sender_id: str
    chat_id: str
    content: str
    media: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to be sent to a platform channel."""

    channel: str
    chat_id: str
    content: str
    media: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
