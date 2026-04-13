"""mocode - minimal claude code alternative"""

__version__ = "0.2.0"

from .core.orchestrator import MocodeCore
from .core import EventBus, EventType, Event
from .core import Config, AsyncAgent
from .core import PermissionChecker, CheckOutcome, CheckResult, PermissionHandler, DefaultPermissionHandler, DenyAllPermissionHandler, PermissionConfig
from .core import InterruptToken
from .core.session import Session, SessionManager
from .core.prompt import PromptBuilder, Section, default_prompt, minimal_prompt

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
    "Section",
    "default_prompt",
    "minimal_prompt",
]
