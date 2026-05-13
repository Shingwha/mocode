"""Gateway — multi-channel message router.

Routes inbound messages from channels to per-user AgentLoop instances,
collects responses, and sends outbound messages back to channels.
Gateway does NOT create AgentLoop — the caller provides an
`agent_factory` callable that builds them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import AsyncGenerator, Callable

from ..channels.protocol import Channel
from ..channels.types import InboundMessage, OutboundMessage
from ..core.agent import AgentLoop
from .session import FileSessionStore, SessionManager, SessionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# send_file tool + PendingMedia + chat_session
# ---------------------------------------------------------------------------


class PendingMedia:
    """Collects media file paths queued by send_file during a chat call."""

    def __init__(self) -> None:
        self.paths: list[str] = []


_current_media: ContextVar[PendingMedia | None] = ContextVar(
    "_current_media", default=None
)
_current_chat_id: ContextVar[str] = ContextVar("_current_chat_id", default="")
_current_channel: ContextVar[str] = ContextVar("_current_channel", default="")


@dataclass
class ChatContext:
    """Values needed to set up the gateway tool context for a chat call."""

    session_key: str
    channel: str
    chat_id: str


@asynccontextmanager
async def chat_session(
    ctx: ChatContext,
) -> AsyncGenerator[PendingMedia, None]:
    """Async context manager that sets/resets gateway ContextVars."""
    pending = PendingMedia()
    tokens = [
        _current_media.set(pending),
        _current_chat_id.set(ctx.chat_id),
        _current_channel.set(ctx.channel),
    ]
    try:
        yield pending
    finally:
        for token in tokens:
            token.var.reset(token)


def register_gateway_tools(registry) -> None:
    """Register gateway-specific tools (send_file) onto the given ToolRegistry."""

    from ..core.tool import Tool

    def send_file_handler(args: dict) -> str:
        path = args.get("path", "")
        if not path:
            return "Error: path is required"
        media = _current_media.get()
        if media is None:
            return "Error: send_file only works in gateway mode"
        media.paths.append(path)
        return f"File queued for sending: {path}"

    registry.register(
        Tool(
            name="send_file",
            description="Send a file to the user. Use this to deliver generated images, documents, or other files.",
            params={
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to send",
                }
            },
            func=send_file_handler,
        )
    )


# ---------------------------------------------------------------------------
# Message Bus
# ---------------------------------------------------------------------------


class _MessageBus:
    """Async queue-based bus connecting channels and core processing."""

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()


# ---------------------------------------------------------------------------
# Per-user session
# ---------------------------------------------------------------------------


@dataclass
class _UserSession:
    session_key: str
    agent: AgentLoop
    sessions: SessionManager | None
    lock: asyncio.Lock
    last_active: float = 0.0


# ---------------------------------------------------------------------------
# Agent factory type
# ---------------------------------------------------------------------------

AgentFactory = Callable[[str], AgentLoop]


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class Gateway:
    """Multi-channel message router.

    Gateway is a pure routing layer — it does NOT create AgentLoop.
    The caller provides an `agent_factory(session_key) -> AgentLoop` that builds
    a fully configured AgentLoop for each new user session.

    Optionally pass a `session_store` for conversation persistence.
    If None, sessions are ephemeral (no persistence).

    Usage::

        def my_factory(session_key: str) -> AgentLoop:
            provider = OpenAIProvider(...)
            tools = ToolRegistry()
            tools.register(bash_tool)
            register_gateway_tools(tools)
            return Agent().provider(provider).prompt(prompt).tools(tools).build()

        gateway = Gateway(
            channels={"weixin": weixin_channel},
            agent_factory=my_factory,
            session_store=FileSessionStore(),
        )
        await gateway.run()
    """

    def __init__(
        self,
        channels: dict[str, Channel],
        agent_factory: AgentFactory,
        session_store: SessionStore | None = None,
        max_users: int = 100,
    ) -> None:
        self._agent_factory = agent_factory
        self._session_store = session_store
        self._max_users = max_users
        self._channels = channels
        self._sessions: dict[str, _UserSession] = {}
        self._bus = _MessageBus()
        self._tasks: list[asyncio.Task] = []

    async def run(self) -> None:
        """Start all channels and dispatch loops."""
        self._tasks.append(asyncio.create_task(self._dispatch_inbound()))
        self._tasks.append(asyncio.create_task(self._dispatch_outbound()))

        for ch in self._channels.values():
            self._tasks.append(
                asyncio.create_task(ch.start(self._on_channel_message))
            )
            logger.info("Started channel: %s", ch.name)

        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        """Stop all channels and cancel tasks."""
        for task in self._tasks:
            task.cancel()
        for ch in self._channels.values():
            try:
                await ch.stop()
            except Exception as e:
                logger.error("Error stopping channel %s: %s", ch.name, e)
        for session in self._sessions.values():
            self._save_session(session)
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("Gateway shutdown complete")

    # -- Channel callback --

    async def _on_channel_message(self, msg) -> None:
        """Callback for channels to deliver inbound messages."""
        if isinstance(msg, InboundMessage):
            await self._bus.inbound.put(msg)
            return
        inbound = InboundMessage(
            channel=getattr(msg, "channel", ""),
            sender_id=getattr(msg, "sender_id", ""),
            chat_id=getattr(msg, "chat_id", ""),
            content=getattr(msg, "content", ""),
            media=getattr(msg, "media", []),
            metadata=getattr(msg, "metadata", {}),
        )
        await self._bus.inbound.put(inbound)

    # -- Dispatch loops --

    async def _dispatch_inbound(self) -> None:
        """Consume inbound messages, run through agent, publish outbound."""
        while True:
            try:
                msg = await self._bus.inbound.get()
                logger.info("[inbound] %s: %s", msg.session_key, msg.content[:100])

                session = self._get_or_create(msg.session_key)

                async with session.lock:
                    try:
                        async with chat_session(
                            ChatContext(
                                session_key=msg.session_key,
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                            )
                        ) as pending:
                            image_paths = [
                                p for p in (msg.media or []) if self._is_image(p)
                            ]
                            response = await session.agent.chat(
                                msg.content, images=image_paths or None
                            )

                        media_to_send = pending.paths

                        if response:
                            logger.info(
                                "[outbound] %s: %s",
                                msg.session_key,
                                response[:200],
                            )
                            await self._bus.outbound.put(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content=response,
                                    metadata=msg.metadata,
                                )
                            )

                        for media_path in media_to_send:
                            await self._bus.outbound.put(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="",
                                    media=[media_path],
                                )
                            )

                        self._save_session(session)
                    except Exception as e:
                        logger.error("[error] %s: %s", msg.session_key, e)
                        await self._bus.outbound.put(
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
                msg = await self._bus.outbound.get()
                channel = self._channels.get(msg.channel)
                if channel is None:
                    logger.error("Unknown channel for outbound: %s", msg.channel)
                    continue
                await self._send_with_retry(channel, msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Outbound dispatch error: %s", e)

    @staticmethod
    async def _send_with_retry(
        channel: Channel, msg: OutboundMessage, max_retries: int = 3
    ) -> None:
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
                delay = 2**attempt
                logger.warning(
                    "Send to %s failed (attempt %d/%d), retrying in %ds: %s",
                    channel.name,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)

    # -- Session management --

    def _get_or_create(self, session_key: str) -> _UserSession:
        session = self._sessions.get(session_key)
        if session is None:
            self._evict_if_needed()
            session = self._create_session(session_key)
            self._sessions[session_key] = session
        session.last_active = time.time()
        return session

    def _create_session(self, session_key: str) -> _UserSession:
        logger.info("Creating session: %s", session_key)
        agent = self._agent_factory(session_key)

        sessions: SessionManager | None = None
        if self._session_store:
            workdir = session_key.replace(":", "/")
            sessions = SessionManager(workdir, self._session_store)

            existing = sessions.list()
            gateway_sessions = [
                s
                for s in existing
                if s.metadata.get("gateway_session_key") == session_key
            ]
            if gateway_sessions:
                session = sessions.resume(gateway_sessions[0].id)
                if session:
                    agent.messages = session.messages
                    logger.info(
                        "Resumed session %s for %s",
                        gateway_sessions[0].id,
                        session_key,
                    )

        return _UserSession(
            session_key=session_key,
            agent=agent,
            sessions=sessions,
            lock=asyncio.Lock(),
            last_active=time.time(),
        )

    def _save_session(self, session: _UserSession) -> None:
        if not session.sessions:
            return
        try:
            session.sessions.mark_dirty()
            session.sessions.save(
                session.agent.messages,
                metadata={"gateway_session_key": session.session_key},
            )
        except Exception as e:
            logger.warning(
                "Failed to save session %s: %s", session.session_key, e
            )

    def _evict_if_needed(self) -> None:
        if len(self._sessions) < self._max_users:
            return
        lru_key = min(self._sessions, key=lambda k: self._sessions[k].last_active)
        logger.info("Evicting LRU session: %s", lru_key)
        self._remove_session(lru_key)

    def _remove_session(self, session_key: str) -> None:
        session = self._sessions.pop(session_key, None)
        if session is None:
            return
        self._save_session(session)

    @staticmethod
    def _is_image(path: str) -> bool:
        from pathlib import Path as P

        return P(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
