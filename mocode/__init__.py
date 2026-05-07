"""mocode v0.2 - minimal claude code alternative

公共 API: App, AppBuilder, Tool, Agent, ...
"""

__version__ = "0.2.0"

from .app import App, AppBuilder
from .event import EventBus, EventType, Event
from .config import Config
from .agent import Agent
from .permission import (
    PermissionChecker,
    CheckOutcome,
    CheckResult,
    PermissionHandler,
    DefaultPermissionHandler,
    DenyAllPermissionHandler,
    PermissionConfig,
)
from .interrupt import CancellationToken, Interrupted, InterruptReason
from .store import Session
from .session import SessionManager
from .prompt import PromptBuilder, Section, system_prompt, xml_tag
from .tool import Tool, ToolRegistry
from .provider import Provider, Response, ToolCall, Usage

__all__ = [
    # Main entry point
    "App",
    "AppBuilder",
    # Provider
    "Provider",
    "Response",
    "ToolCall",
    "Usage",
    # Core
    "EventBus",
    "EventType",
    "Event",
    "Config",
    "Agent",
    # Tool
    "Tool",
    "ToolRegistry",
    # Permission
    "PermissionChecker",
    "CheckOutcome",
    "CheckResult",
    "PermissionHandler",
    "DefaultPermissionHandler",
    "DenyAllPermissionHandler",
    "PermissionConfig",
    # Interrupt
    "CancellationToken",
    "Interrupted",
    "InterruptReason",
    # Session
    "Session",
    "SessionManager",
    # Prompt system
    "PromptBuilder",
    "Section",
    "system_prompt",
    "xml_tag",
]
