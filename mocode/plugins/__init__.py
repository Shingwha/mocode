"""Plugin system for mocode

Provides a hook/plugin architecture for extending mocode functionality.

Usage:
    from mocode.plugins import Plugin, Hook, HookPoint, hook

    # Create a plugin
    class MyPlugin(Plugin):
        def on_load(self): print("Loaded")
        def on_enable(self): print("Enabled")
        def on_disable(self): print("Disabled")
        def on_unload(self): print("Unloaded")

    # Create a hook using decorator
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
from .decorators import HookBuilder, async_hook, hook
from .loader import PluginLoader
from .manager import PluginManager
from .registry import HookRegistry, PluginRegistry

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
]
