"""Channel manager - core dispatcher with retry logic"""

import asyncio
import logging

from .bus import MessageBus, OutboundMessage
from .base import BaseChannel
from .router import UserRouter
from .tools import _current_core

logger = logging.getLogger(__name__)


class ChannelManager:
    """Dispatches messages between channels and core processing.

    Manages the lifecycle of all channels and routes messages
    through the MessageBus with retry on outbound failures.
    """

    def __init__(self, bus: MessageBus, router: UserRouter):
        self._bus = bus
        self._router = router
        self._channels: dict[str, BaseChannel] = {}
        self._tasks: list[asyncio.Task] = []

    def register(self, channel: BaseChannel) -> None:
        """Register a channel."""
        self._channels[channel.name] = channel
        logger.info("Registered channel: %s", channel.name)

    async def start_all(self) -> None:
        """Start inbound/outbound dispatchers and all channels."""
        self._tasks.append(asyncio.create_task(self._dispatch_inbound()))
        self._tasks.append(asyncio.create_task(self._dispatch_outbound()))
        for channel in self._channels.values():
            self._tasks.append(asyncio.create_task(channel.start()))
            logger.info("Started channel: %s", channel.name)

    async def stop_all(self) -> None:
        """Stop all channels and cancel dispatch tasks."""
        for task in self._tasks:
            task.cancel()
        for channel in self._channels.values():
            try:
                await channel.stop()
            except Exception as e:
                logger.error("Error stopping channel %s: %s", channel.name, e)
        await self._router.shutdown_all()
        # Wait for tasks to finish cancellation
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("All channels stopped")

    async def _dispatch_inbound(self) -> None:
        """Consume inbound messages, run through core, publish outbound."""
        while True:
            try:
                msg = await self._bus.consume_inbound()
                logger.info(
                    "[inbound] %s: %s", msg.session_key, msg.content[:100]
                )
                session = self._router.get_or_create(msg.session_key)

                async with session.lock:
                    try:
                        # Set context for gateway tools (send_file)
                        token = _current_core.set(session.core)
                        try:
                            response = await session.core.chat(
                                msg.content, media=msg.media or None
                            )
                        finally:
                            _current_core.reset(token)

                        # Collect pending media from tools
                        pending = getattr(session.core, "_pending_media", [])
                        media_to_send = list(pending)
                        pending.clear()

                        if response:
                            logger.info(
                                "[outbound] %s: %s",
                                msg.session_key,
                                response[:200],
                            )
                            await self._bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content=response,
                                    metadata=msg.metadata,
                                )
                            )
                        # Send any media queued by send_file tool
                        for media_path in media_to_send:
                            await self._bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="",
                                    media=[media_path],
                                )
                            )
                    except Exception as e:
                        logger.error(
                            "[error] %s: %s", msg.session_key, e
                        )
                        await self._bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=f"[Error] {e}",
                                metadata=msg.metadata,
                            )
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Inbound dispatch error: %s", e)

    async def _dispatch_outbound(self) -> None:
        """Consume outbound messages and send via channel with retry."""
        while True:
            try:
                msg = await self._bus.consume_outbound()
                channel = self._channels.get(msg.channel)
                if channel is None:
                    logger.error("Unknown channel for outbound: %s", msg.channel)
                    continue
                await self._send_with_retry(channel, msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Outbound dispatch error: %s", e)

    async def _send_with_retry(
        self, channel: BaseChannel, msg: OutboundMessage, max_retries: int = 3
    ) -> None:
        """Send message with exponential backoff retry."""
        for attempt in range(max_retries + 1):
            try:
                await channel.send(msg)
                return
            except Exception as e:
                if attempt >= max_retries:
                    logger.error(
                        "Failed to send to %s after %d attempts: %s",
                        channel.name,
                        max_retries + 1,
                        e,
                    )
                    return
                delay = 2**attempt  # 1s, 2s, 4s
                logger.warning(
                    "Send to %s failed (attempt %d/%d), retrying in %ds: %s",
                    channel.name,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
