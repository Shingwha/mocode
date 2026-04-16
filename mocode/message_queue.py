"""MessageQueue — 不变"""

import asyncio
from collections import deque
from typing import Callable

from .event import EventBus, EventType


class MessageQueue:
    """Manages message queue and processing state for external injections"""

    def __init__(
        self,
        chat_fn: Callable,
        event_bus: EventBus,
        mark_unsaved_fn: Callable | None = None,
    ):
        self._chat_fn = chat_fn
        self._event_bus = event_bus
        self._mark_unsaved = mark_unsaved_fn
        self._queue: deque[tuple[str, str | None]] = deque()
        self._is_processing: bool = False
        self._queue_lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_processing(self) -> bool:
        return self._is_processing

    async def inject(
        self, message: str, conversation_id: str | None = None
    ) -> str:
        async with self._queue_lock:
            self._is_processing = True
            try:
                if self._mark_unsaved:
                    self._mark_unsaved()
                return await self._chat_fn(message, conversation_id)
            finally:
                self._is_processing = False
                self._event_bus.emit(EventType.AGENT_IDLE, None)
                await self._process_next()

    def enqueue(self, message: str, conversation_id: str | None = None) -> None:
        self._queue.append((message, conversation_id))
        if not self._is_processing:
            asyncio.create_task(self._process_next())

    async def _process_next(self) -> None:
        if self._is_processing or not self._queue:
            return

        message, conv_id = self._queue.popleft()
        await self.inject(message, conv_id)
