"""Plugin system for mocode

Provides a hook/plugin architecture for extending mocode functionality.

Usage:
    from mocode.plugins import Plugin, HookBase, HookPoint, hook

    # Create a plugin
    class MyPlugin(Plugin):
        async def on_load(self): print("Loaded")
        async def on_enable(self): print("Enabled")
        async def on_disable(self): print("Disabled")
        async def on_unload(self): print("Unloaded")

    # Create a hook by inheriting HookBase (recommended)
    class MyHook(HookBase):
        _name = "my-hook"
        _hook_point = HookPoint.TOOL_AFTER_RUN
        _priority = 25

        def execute(self, context: HookContext) -> HookContext:
            print(f"Tool executed: {context.data['name']}")
            return context

    # Or create a hook using decorator
    @hook(HookPoint.TOOL_AFTER_RUN, priority=25)
    def log_tool(context: HookContext) -> HookContext:
        print(f"Tool executed: {context.data['name']}")
        return context
"""

from .base import (
    Hook,
    HookBase,
    HookContext,
    HookPoint,
    Plugin,
    PluginInfo,
    PluginMetadata,
    PluginState,
)
from .context import PluginContext
from .decorators import HookBuilder, async_hook, hook
from .loader import BUILTIN_DIR, PluginLoader
from .manager import PluginManager
from .registry import HookRegistry, PluginRegistry
from .installer import (
    PluginInstaller,
    InstalledPluginInfo,
)
from ..core.installer import InstallCandidate as PluginCandidate
from ..core.installer import InstallResult
from ..core.installer import InstallMethod
from ..core.installer import SourceType as PluginSourceType

__all__ = [
    # Base classes
    "Hook",
    "HookBase",
    "HookContext",
    "HookPoint",
    "Plugin",
    "PluginInfo",
    "PluginMetadata",
    "PluginState",
    # Context
    "PluginContext",
    # Decorators
    "hook",
    "async_hook",
    "HookBuilder",
    # Registry
    "HookRegistry",
    "PluginRegistry",
    # Manager
    "PluginManager",
    "PluginLoader",
    # Installer
    "PluginInstaller",
    "PluginCandidate",
    "InstallResult",
    "InstallMethod",
    "PluginSourceType",
    # Constants
    "BUILTIN_DIR",
]
