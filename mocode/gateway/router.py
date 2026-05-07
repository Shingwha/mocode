"""User router - multi-user session management

v0.2 adaptation:
- MocodeCore → AppBuilder for session creation
- Instance-scoped tool registration (register_gateway_tools, register_cron_tools)
- Removed start_dream_scheduler/stop_dream_scheduler (DreamManager auto-created by AppBuilder)
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import Config
from ..event import Event, EventType
from ..app import AppBuilder
from ..permission import DefaultPermissionHandler
from ..session import FileSessionStore
from ..store import InMemoryConfigStore
from .tools import register_gateway_tools

if TYPE_CHECKING:
    from .cron.scheduler import CronScheduler

logger = logging.getLogger(__name__)


@dataclass
class UserSession:
    """Per-user session state."""

    session_key: str
    core: "App"  # noqa: F821 — App imported at runtime
    lock: asyncio.Lock
    last_active: float = 0.0


class UserRouter:
    """Manages independent App instances per user.

    Each user gets their own App with isolated conversation history
    and agent state. Concurrency is handled per-user with asyncio.Lock.
    """

    def __init__(
        self,
        config: Config,
        gateway_config: dict,
        max_users: int = 100,
        cron_scheduler: "CronScheduler | None" = None,
    ):
        self._base_config = config
        self._gateway_config = gateway_config
        self._max_users = max_users
        self._cron_scheduler = cron_scheduler
        self._sessions: dict[str, UserSession] = {}

    def set_cron_scheduler(self, scheduler: "CronScheduler") -> None:
        """Inject cron scheduler (called by ChannelManager after construction)."""
        self._cron_scheduler = scheduler

    def get_or_create(self, session_key: str) -> UserSession:
        """Get or create a user session."""
        session = self._sessions.get(session_key)
        if session is None:
            self._evict_if_needed()
            session = self._create_session(session_key)
            self._sessions[session_key] = session
        session.last_active = time.time()
        return session

    def _create_session(self, session_key: str) -> UserSession:
        """Create a new user session with isolated App instance."""
        logger.info("Creating session: %s", session_key)

        config_data = self._base_config.to_dict()
        config_data["permission"] = {"*": "allow"}  # yolo-style for gateway

        app = (AppBuilder()
            .with_config(config_data)
            .with_config_store(InMemoryConfigStore())
            .with_session_store(FileSessionStore())
            .with_permission_handler(DefaultPermissionHandler())
            .build())
        app.config.set_mode("yolo")

        # Register gateway-specific tools to this instance
        register_gateway_tools(app.tools)

        # Register cron tools if scheduler is available
        if self._cron_scheduler is not None:
            from .cron.tools import register_cron_tools
            register_cron_tools(app.tools, self._cron_scheduler)

        # Subscribe to per-session events for logging
        self._subscribe_session_events(session_key, app)

        # Resume last session for this user
        user_sessions = [
            s for s in app.sessions.list()
            if s.metadata.get("gateway_session_key") == session_key
        ]
        if user_sessions:
            app.load_session(user_sessions[0].id)
            logger.info("Resumed session %s for %s", user_sessions[0].id, session_key)

        return UserSession(
            session_key=session_key,
            core=app,
            lock=asyncio.Lock(),
            last_active=time.time(),
        )

    def _subscribe_session_events(
        self, session_key: str, core  # core: App
    ) -> dict:
        """Subscribe to EventBus events for a session. Returns handler refs."""
        handlers = {}

        def on_tool_start(event: Event) -> None:
            data = event.data or {}
            name = data.get("name", "?")
            args = data.get("args", {})
            args_str = str(args)[:100]
            logger.info("[tool] %s -> %s(%s)", session_key, name, args_str)

        def on_tool_complete(event: Event) -> None:
            data = event.data or {}
            name = data.get("name", "?")
            result = str(data.get("result", ""))[:200]
            logger.info("[tool-done] %s <- %s: %s", session_key, name, result)

        def on_error(event: Event) -> None:
            data = event.data or {}
            error = str(data.get("error", data))[:200]
            logger.error("[agent-error] %s: %s", session_key, error)

        def on_dream_start(event: Event) -> None:
            data = event.data or {}
            pending = data.get("pending_summaries", 0)
            logger.info("[dream-start] %s (pending: %d)", session_key, pending)

        def on_dream_summary_available(event: Event) -> None:
            data = event.data or {}
            count = data.get("summary_count", 0)
            ids = data.get("summary_ids", [])
            logger.info(
                "[dream-summary] %s: %d summaries ready -> %s",
                session_key,
                count,
                ids[-1] if ids else "?",
            )

        def on_dream_complete(event: Event) -> None:
            data = event.data or {}
            processed = data.get("summaries_processed", 0)
            edits = data.get("edits_made", 0)
            tools = data.get("tool_calls_made", 0)
            snapshot = data.get("snapshot_id", "")
            logger.info(
                "[dream-complete] %s: %d summaries, %d edits, %d tool calls, snapshot=%s",
                session_key,
                processed,
                edits,
                tools,
                snapshot[:8] if snapshot else "none",
            )

        handlers[EventType.TOOL_START] = on_tool_start
        handlers[EventType.TOOL_COMPLETE] = on_tool_complete
        handlers[EventType.ERROR] = on_error
        handlers[EventType.DREAM_START] = on_dream_start
        handlers[EventType.DREAM_SUMMARY_AVAILABLE] = on_dream_summary_available
        handlers[EventType.DREAM_COMPLETE] = on_dream_complete

        for et, handler in handlers.items():
            core.event_bus.on(et, handler)

        return handlers

    def _evict_if_needed(self) -> None:
        """Evict least-recently-active sessions if at capacity."""
        if len(self._sessions) < self._max_users:
            return

        lru_key = min(self._sessions, key=lambda k: self._sessions[k].last_active)
        logger.info("Evicting LRU session: %s", lru_key)
        self._remove_session(lru_key)

    def _remove_session(self, session_key: str) -> None:
        """Remove a user session, saving state first."""
        session = self._sessions.pop(session_key, None)
        if session is None:
            return
        session.core.event_bus.clear()
        try:
            session.core.sessions.save_if_dirty(
                session.core.agent.messages,
                session.core.current_model,
                session.core.current_provider,
            )
        except Exception as e:
            logger.warning("Failed to save session %s: %s", session_key, e)

    async def shutdown_all(self) -> None:
        """Save all sessions and cleanup."""
        for key in list(self._sessions.keys()):
            self._remove_session(key)
        self._sessions.clear()
        logger.info("All user sessions shut down")
