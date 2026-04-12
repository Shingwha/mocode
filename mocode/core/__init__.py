"""Core layer - business logic, independent of UI"""

from .config import Config, PluginConfig, ProviderConfig, CurrentConfig, CompactConfig
from .events import EventBus, EventType, Event
from .agent import AsyncAgent
from .prompt import PromptBuilder, Section, default_prompt, minimal_prompt
from .compact import CompactManager
from .permission import (
    PermissionAction,
    PermissionConfig,
    PermissionChecker,
    CheckOutcome,
    CheckResult,
    PermissionHandler,
    DefaultPermissionHandler,
    DenyAllPermissionHandler,
)
from .interrupt import InterruptToken
from .session import Session, SessionManager
from .plugin_coordinator import PluginCoordinator
from .orchestrator import MocodeCore, SessionState
from .utils import preview_result
from .installer import (
    GitHubInstaller,
    InstallMethod,
    SourceType,
    InstallCandidate,
    InstallResult,
    InstalledItemInfo,
)

__all__ = [
    "Config",
    "PluginConfig",
    "ProviderConfig",
    "CurrentConfig",
    "CompactConfig",
    "EventBus",
    "EventType",
    "Event",
    "AsyncAgent",
    "CompactManager",
    # Prompt system
    "PromptBuilder",
    "Section",
    "default_prompt",
    "minimal_prompt",
    # Permission
    "PermissionAction",
    "PermissionChecker",
    "CheckOutcome",
    "CheckResult",
    "PermissionConfig",
    "PermissionHandler",
    "DefaultPermissionHandler",
    "DenyAllPermissionHandler",
    # Other
    "InterruptToken",
    "Session",
    "SessionManager",
    "SessionState",
    # Coordinators
    "PluginCoordinator",
    "MocodeCore",
    # Installer base
    "GitHubInstaller",
    "InstallMethod",
    "SourceType",
    "InstallCandidate",
    "InstallResult",
    "InstalledItemInfo",
    # Utils
    "preview_result",
]
