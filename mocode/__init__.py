"""mocode - minimal claude code alternative"""

__version__ = "0.2.0"

from .core.orchestrator import MocodeCore
from .core import EventBus, EventType, Event
from .core import Config, AsyncAgent
from .core import PermissionChecker, CheckOutcome, CheckResult, PermissionHandler, DefaultPermissionHandler, DenyAllPermissionHandler, PermissionConfig
from .core import InterruptToken
from .core.session import Session, SessionManager
from .core.prompt import (
    PromptBuilder,
    StaticSection,
    DynamicSection,
    default_prompt,
    minimal_prompt,
    custom_prompt,
)
from .plugins import (
    Hook,
    HookContext,
    HookPoint,
    Plugin,
    PluginManager,
    PluginInfo,
    PluginMetadata,
    PluginState,
    HookRegistry,
    hook,
)

# Backward compatibility alias
MocodeClient = MocodeCore

__all__ = [
    # Main entry point
    "MocodeCore",
    "MocodeClient",
    # Core
    "EventBus",
    "EventType",
    "Event",
    "Config",
    "AsyncAgent",
    # Permission
    "PermissionChecker",
    "CheckOutcome",
    "CheckResult",
    "PermissionHandler",
    "DefaultPermissionHandler",
    "DenyAllPermissionHandler",
    "PermissionConfig",
    # Interrupt
    "InterruptToken",
    # Session
    "Session",
    "SessionManager",
    # Prompt system
    "PromptBuilder",
    "StaticSection",
    "DynamicSection",
    "default_prompt",
    "minimal_prompt",
    "custom_prompt",
    # Plugin system
    "Hook",
    "HookContext",
    "HookPoint",
    "Plugin",
    "PluginManager",
    "PluginInfo",
    "PluginMetadata",
    "PluginState",
    "HookRegistry",
    "hook",
]
