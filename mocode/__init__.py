"""mocode - minimal claude code alternative"""

__version__ = "0.2.0"

from .sdk import MocodeClient
from .core import EventBus, EventType, Event, get_event_bus
from .core import Config, AsyncAgent
from .core import PermissionMatcher
from .core import PermissionHandler, DefaultPermissionHandler
from .core import InterruptToken
from .core.session import Session, SessionManager

# Gateway (lazy import to avoid dependency issues)
def get_gateway():
    """获取 Gateway 模块（延迟导入）"""
    from . import gateway
    return gateway

__all__ = [
    # SDK
    "MocodeClient",
    # Core
    "EventBus",
    "EventType",
    "Event",
    "get_event_bus",
    "Config",
    "AsyncAgent",
    # Permission
    "PermissionMatcher",
    "PermissionHandler",
    "DefaultPermissionHandler",
    # Interrupt
    "InterruptToken",
    # Session
    "Session",
    "SessionManager",
    # Gateway
    "get_gateway",
]
