"""User router - multi-user session management"""

import asyncio
import logging
import time
from dataclasses import asdict, dataclass

from ..core.config import Config
from ..core.orchestrator import MocodeCore

logger = logging.getLogger(__name__)


@dataclass
class UserSession:
    """Per-user session state."""

    session_key: str
    core: MocodeCore
    lock: asyncio.Lock
    last_active: float = 0.0


class UserRouter:
    """Manages independent MocodeCore instances per user.

    Each user gets their own MocodeCore with isolated conversation history
    and agent state. Concurrency is handled per-user with asyncio.Lock.
    """

    def __init__(self, config: Config, gateway_config: dict, max_users: int = 100):
        self._base_config = config
        self._gateway_config = gateway_config
        self._max_users = max_users
        self._sessions: dict[str, UserSession] = {}

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
        """Create a new user session with isolated MocodeCore."""
        logger.info("Creating session: %s", session_key)

        config_data = {
            "current": asdict(self._base_config.current),
            "providers": {k: asdict(v) for k, v in self._base_config.providers.items()},
            "permission": {"*": "allow"},  # yolo-style for gateway
            "tool_result_limit": self._base_config.tool_result_limit,
        }

        core = MocodeCore(
            config=config_data,
            persistence=False,
            auto_discover_plugins=False,
        )
        core.config.set_mode("yolo")

        return UserSession(
            session_key=session_key,
            core=core,
            lock=asyncio.Lock(),
            last_active=time.time(),
        )

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
        if session and session.core.has_unsaved_changes:
            try:
                session.core.save_session()
            except Exception as e:
                logger.warning("Failed to save session %s: %s", session_key, e)

    async def shutdown_all(self) -> None:
        """Save all sessions and cleanup."""
        for key in list(self._sessions.keys()):
            self._remove_session(key)
        self._sessions.clear()
        logger.info("All user sessions shut down")
