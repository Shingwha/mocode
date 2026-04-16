"""EventBus + EventType + Event

v0.2 改进：删除 TEXT_STREAMING 和 TEXT_DELTA（非流式架构不需要）。
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Union

logger = logging.getLogger(__name__)

Handler = Union[Callable[["Event"], None], Callable[["Event"], Awaitable[None]]]


class EventType(Enum):
    TEXT_COMPLETE = auto()
    TOOL_START = auto()
    TOOL_COMPLETE = auto()
    TOOL_PROGRESS = auto()
    MESSAGE_ADDED = auto()
    MODEL_CHANGED = auto()
    ERROR = auto()
    STATUS_UPDATE = auto()
    PERMISSION_ASK = auto()
    INTERRUPTED = auto()
    CONTEXT_COMPACT = auto()
    AGENT_IDLE = auto()
    DREAM_START = auto()
    DREAM_SUMMARY_AVAILABLE = auto()
    DREAM_COMPLETE = auto()
    COMPONENT_STATE_CHANGE = auto()
    COMPONENT_COMPLETE = auto()


@dataclass
class Event:
    type: EventType
    data: Any = None


class EventBus:
    """事件总线 - 支持实例化，支持异步处理器"""

    def __init__(self):
        self._handlers: dict[EventType, list[tuple[int, Handler]]] = {
            et: [] for et in EventType
        }

    def on(self, event_type: EventType, handler: Handler, priority: int = 50):
        self._handlers[event_type].append((priority, handler))
        self._handlers[event_type].sort(key=lambda x: x[0])
        return self

    def off(self, event_type: EventType, handler: Handler):
        self._handlers[event_type] = [
            (p, h) for p, h in self._handlers[event_type] if h is not handler
        ]
        return self

    def emit(self, event_type: EventType, data: Any = None):
        event = Event(event_type, data)
        for _, handler in self._handlers[event_type][:]:
            try:
                result = handler(event)
                if inspect.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                logger.exception(f"Event handler error: {e}")

    async def emit_async(self, event_type: EventType, data: Any = None):
        event = Event(event_type, data)
        for _, handler in self._handlers[event_type][:]:
            try:
                result = handler(event)
                if inspect.iscoroutine(result):
                    await result
            except Exception as e:
                logger.exception(f"Event handler error: {e}")

    def clear(self):
        for handlers in self._handlers.values():
            handlers.clear()
