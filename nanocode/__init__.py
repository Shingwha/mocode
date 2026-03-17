"""nanocode - minimal claude code alternative"""

__version__ = "0.2.0"

from .sdk import NanoCodeClient
from .core import EventBus, EventType, Event, get_event_bus
from .core import Config, AsyncAgent
from .core import PermissionHandler, DefaultPermissionHandler

# Gateway (lazy import to avoid dependency issues)
def get_gateway():
    """获取 Gateway 模块（延迟导入）"""
    from . import gateway
    return gateway

__all__ = [
    # SDK
    "NanoCodeClient",
    # Core
    "EventBus",
    "EventType",
    "Event",
    "get_event_bus",
    "Config",
    "AsyncAgent",
    # Permission
    "PermissionHandler",
    "DefaultPermissionHandler",
    # Gateway
    "get_gateway",
]
