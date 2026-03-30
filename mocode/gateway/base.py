"""Base gateway abstract class"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Callable

from ..core.config import Config
from .router import UserRouter

logger = logging.getLogger(__name__)


class BaseGateway(ABC):
    """Abstract base class for platform gateways.

    Subclasses must implement _setup(), _run(), _teardown(), and _send_typing().
    """

    def __init__(self, config: Config, gateway_config: dict):
        self._config = config
        self._gateway_config = gateway_config
        self._router = UserRouter(config, gateway_config)
        self._running = False

    async def start(self) -> None:
        """Start gateway: setup platform, then run main loop."""
        self._running = True
        logger.info("Gateway starting: %s", self.__class__.__name__)
        await self._setup()
        await self._run()

    async def stop(self) -> None:
        """Stop gateway: teardown platform and shutdown all user sessions."""
        self._running = False
        logger.info("Gateway stopping: %s", self.__class__.__name__)
        await self._teardown()
        await self._router.shutdown_all()

    async def handle_message(
        self, user_id: str, text: str, reply_fn: Callable
    ) -> None:
        """Handle an incoming message from a platform user.

        Args:
            user_id: Platform-specific user identifier.
            text: Message text.
            reply_fn: Async callable to send reply back to user.
        """
        core = self._router.get_or_create(user_id).core
        await self._send_typing(user_id, True)
        try:
            response = await core.chat(text)
            await reply_fn(response or "")
        except Exception as e:
            logger.error("Error handling message from %s: %s", user_id, e)
            await reply_fn(f"[Error] {e}")
        finally:
            await self._send_typing(user_id, False)

    # Lifecycle hooks for subclasses

    async def _setup(self) -> None:
        """Platform initialization (login, connect, etc.)."""
        ...

    async def _run(self) -> None:
        """Platform main loop (polling, listening, etc.)."""
        ...

    async def _teardown(self) -> None:
        """Platform cleanup."""
        ...

    @abstractmethod
    async def _send_typing(self, user_id: str, is_typing: bool) -> None:
        """Send typing indicator to user."""
        ...
