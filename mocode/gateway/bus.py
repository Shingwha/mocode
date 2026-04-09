"""Message bus for decoupling channels from core processing"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class InboundMessage:
    """Message from a platform channel."""

    channel: str
    sender_id: str
    chat_id: str
    content: str
    media: dict = field(default_factory=dict)
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
    metadata: dict = field(default_factory=dict)


class MessageBus:
    """Async queue-based bus connecting channels and core processing."""

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()
