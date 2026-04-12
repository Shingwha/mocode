"""事件系统 - 解耦 Agent 与 UI"""

import asyncio
import inspect
import logging
from enum import Enum, auto
from typing import Callable, Any, Awaitable, Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)

Handler = Union[Callable[["Event"], None], Callable[["Event"], Awaitable[None]]]


class EventType(Enum):
    """事件类型"""

    TEXT_STREAMING = auto()  # 文本流式输出开始
    TEXT_DELTA = auto()  # 文本增量更新
    TEXT_COMPLETE = auto()  # 文本完成
    TOOL_START = auto()  # 工具开始执行
    TOOL_COMPLETE = auto()  # 工具执行完成
    TOOL_PROGRESS = auto()  # 工具执行进度
    MESSAGE_ADDED = auto()  # 消息添加
    MODEL_CHANGED = auto()  # 模型切换
    ERROR = auto()  # 错误
    STATUS_UPDATE = auto()  # 状态栏更新
    PERMISSION_ASK = auto()  # 权限询问
    INTERRUPTED = auto()  # 中断完成
    CONTEXT_COMPACT = auto()  # 上下文压缩
    AGENT_IDLE = auto()  # Agent 空闲（可用于处理队列消息）
    # Component events
    COMPONENT_STATE_CHANGE = auto()  # 组件状态变化
    COMPONENT_COMPLETE = auto()  # 组件完成


@dataclass
class Event:
    """事件对象"""

    type: EventType
    data: Any = None


class EventBus:
    """事件总线 - 支持实例化，支持异步处理器"""

    def __init__(self):
        """初始化事件总线"""
        self._handlers: dict[EventType, list[tuple[int, Handler]]] = {
            et: [] for et in EventType
        }

    def on(self, event_type: EventType, handler: Handler, priority: int = 50):
        """订阅事件，支持优先级（数值越小越先执行）"""
        self._handlers[event_type].append((priority, handler))
        self._handlers[event_type].sort(key=lambda x: x[0])
        return self  # 链式调用

    def off(self, event_type: EventType, handler: Handler):
        """取消订阅"""
        self._handlers[event_type] = [
            (p, h) for p, h in self._handlers[event_type] if h is not handler
        ]
        return self

    def emit(self, event_type: EventType, data: Any = None):
        """同步发射 - 自动调度异步处理器"""
        event = Event(event_type, data)
        for _, handler in self._handlers[event_type][:]:
            try:
                result = handler(event)
                if inspect.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                logger.exception(f"Event handler error: {e}")

    async def emit_async(self, event_type: EventType, data: Any = None):
        """异步发射 - 等待所有处理器完成"""
        event = Event(event_type, data)
        for _, handler in self._handlers[event_type][:]:
            try:
                result = handler(event)
                if inspect.iscoroutine(result):
                    await result
            except Exception as e:
                logger.exception(f"Event handler error: {e}")

    def clear(self):
        """清空所有订阅"""
        for handlers in self._handlers.values():
            handlers.clear()
