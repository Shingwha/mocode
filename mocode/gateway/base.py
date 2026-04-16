"""Base channel abstract class"""

import logging
from abc import ABC, abstractmethod

from ..config import Config
from .bus import InboundMessage, MessageBus, OutboundMessage

logger = logging.getLogger(__name__)


class BaseChannel(ABC):
    """Abstract base class for platform channels.

    Subclasses must implement start(), stop(), and send().
    """

    def __init__(
        self,
        name: str,
        config: Config,
        gateway_config: dict,
        bus: MessageBus,
    ):
        self.name = name
        self._config = config
        self._gateway_config = gateway_config
        self._bus = bus

    def is_allowed(self, sender_id: str) -> bool:
        """Check if sender is allowed to use the gateway."""
        allow_from = self._gateway_config.get("allow_from", ["*"])
        if "*" in allow_from:
            return True
        return sender_id in allow_from

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Check permission and publish inbound message to bus."""
        if not self.is_allowed(sender_id):
            logger.warning("Blocked message from unauthorized sender: %s", sender_id)
            return

        msg = InboundMessage(
            channel=self.name,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata=metadata or {},
        )
        await self._bus.publish_inbound(msg)

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None: ...
