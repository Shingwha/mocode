"""事件系统 - 解耦 Agent 与 UI"""

from enum import Enum, auto
from typing import Callable, Any
from dataclasses import dataclass


class EventType(Enum):
    """事件类型"""
    TEXT_STREAMING = auto()   # 文本流式输出开始
    TEXT_DELTA = auto()       # 文本增量更新
    TEXT_COMPLETE = auto()    # 文本完成
    TOOL_START = auto()       # 工具开始执行
    TOOL_COMPLETE = auto()    # 工具执行完成
    TOOL_PROGRESS = auto()    # 工具执行进度
    MESSAGE_ADDED = auto()    # 消息添加
    MODEL_CHANGED = auto()    # 模型切换
    ERROR = auto()            # 错误
    STATUS_UPDATE = auto()    # 状态栏更新
    PERMISSION_ASK = auto()   # 权限询问
    INTERRUPTED = auto()      # 中断完成


@dataclass
class Event:
    """事件对象"""
    type: EventType
    data: Any = None


class EventBus:
    """事件总线 - 支持实例化，用于多租户场景"""

    def __init__(self):
        """初始化事件总线"""
        self._handlers: dict[EventType, list[Callable[[Event], None]]] = {
            et: [] for et in EventType
        }

    def on(self, event_type: EventType, handler: Callable[[Event], None]):
        """订阅事件"""
        self._handlers[event_type].append(handler)
        return self  # 链式调用

    def off(self, event_type: EventType, handler: Callable[[Event], None]):
        """取消订阅"""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
        return self

    def emit(self, event_type: EventType, data: Any = None):
        """发送事件"""
        event = Event(event_type, data)
        for handler in self._handlers[event_type][:]:
            handler(event)

    def clear(self):
        """清空所有订阅"""
        for handlers in self._handlers.values():
            handlers.clear()


# 全局默认事件总线实例（向后兼容）
_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """获取默认事件总线实例（延迟初始化）"""
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


# 向后兼容：保留全局 events 变量作为属性访问
class _EventsProxy:
    """代理类，向后兼容 events 全局变量"""

    def __getattr__(self, name):
        return getattr(get_event_bus(), name)


events = _EventsProxy()
