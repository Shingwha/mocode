"""Heartbeat service - periodically reads HEARTBEAT.md and feeds tasks to agent"""

import asyncio
import logging
from pathlib import Path

from ...paths import HEARTBEAT_FILE
from ..bus import MessageBus, OutboundMessage
from ..router import UserRouter
from ..tools import PendingMedia, _current_core, _current_media

logger = logging.getLogger(__name__)


class HeartbeatService:
    def __init__(
        self,
        router: UserRouter,
        bus: MessageBus,
        config: dict | None = None,
    ):
        config = config or {}
        self._router = router
        self._bus = bus
        self._enabled = config.get("enabled", True)
        self._interval_s = config.get("intervalS", 1800)
        self._task: asyncio.Task | None = None
        self._last_content_hash: int = 0
        self._last_active_channel: str = ""
        self._last_active_chat_id: str = ""
        self._last_active_session_key: str = ""

    def update_last_active(self, channel: str, chat_id: str, session_key: str) -> None:
        self._last_active_channel = channel
        self._last_active_chat_id = chat_id
        self._last_active_session_key = session_key

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Heartbeat disabled")
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (interval=%ds)", self._interval_s)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            try:
                if not self._last_active_channel:
                    continue
                content = _read_heartbeat_file()
                if not content:
                    continue
                content_hash = hash(content)
                if content_hash == self._last_content_hash:
                    continue
                self._last_content_hash = content_hash

                session = self._router.get_or_create(self._last_active_session_key)
                async with session.lock:
                    pending = PendingMedia()
                    core_token = _current_core.set(session.core)
                    media_token = _current_media.set(pending)
                    try:
                        prompt = (
                            "以下是 HEARTBEAT.md 中定义的定期任务。"
                            "请逐一检查，判断哪些现在需要执行。"
                            "如果某个任务现在不需要执行，说明原因并跳过。\n\n"
                            "---\n" + content
                        )
                        response = await session.core.chat(prompt)
                    finally:
                        _current_core.reset(core_token)
                        _current_media.reset(media_token)

                    if response:
                        await self._bus.publish_outbound(OutboundMessage(
                            channel=self._last_active_channel,
                            chat_id=self._last_active_chat_id,
                            content=response,
                        ))
                    for media_path in pending.paths:
                        await self._bus.publish_outbound(OutboundMessage(
                            channel=self._last_active_channel,
                            chat_id=self._last_active_chat_id,
                            content="",
                            media=[media_path],
                        ))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Heartbeat error: %s", e)


def _read_heartbeat_file() -> str:
    """Read HEARTBEAT.md content, return empty string if not found."""
    try:
        if HEARTBEAT_FILE.exists():
            return HEARTBEAT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""
