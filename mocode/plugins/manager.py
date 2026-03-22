"""Plugin Manager - Lifecycle management"""

from typing import TYPE_CHECKING

from .base import Hook, Plugin, PluginInfo, PluginState
from .loader import BUILTIN_DIR, PluginLoader
from .registry import HookRegistry, PluginRegistry

if TYPE_CHECKING:
    from ..tools.base import Tool
    from ..cli.commands.base import Command
    from ..core.prompt import PromptSection


class PluginManager:
    """Manages plugin lifecycle and integration"""

    def __init__(
        self,
        hook_registry: HookRegistry | None = None,
        plugin_registry: PluginRegistry | None = None,
        loader: PluginLoader | None = None,
    ):
        """Initialize plugin manager

        Args:
            hook_registry: Hook registry instance (creates new if None)
            plugin_registry: Plugin registry instance (creates new if None)
            loader: Plugin loader instance (creates new if None)
        """
        self.hook_registry = hook_registry or HookRegistry()
        self.plugin_registry = plugin_registry or PluginRegistry()
        self.loader = loader or PluginLoader()

        # Track registered hooks/tools/commands by plugin
        self._plugin_hooks: dict[str, list[str]] = {}
        self._plugin_tools: dict[str, list[str]] = {}
        self._plugin_commands: dict[str, list[str]] = {}

    def discover(self) -> list[PluginInfo]:
        """Discover all available plugins

        Returns:
            List of discovered plugin infos
        """
        plugins = self.loader.discover()

        for info in plugins:
            self.plugin_registry.register(info)

        return plugins

    def discover_and_enable_builtins(
        self, disabled_list: list[str] | None = None
    ) -> list[PluginInfo]:
        """Discover plugins and auto-enable builtin plugins

        Args:
            disabled_list: List of plugin names to skip auto-enabling

        Returns:
            List of discovered plugin infos
        """
        plugins = self.discover()
        disabled_list = disabled_list or []

        # Auto-enable builtin plugins (unless in disabled list)
        for info in plugins:
            if str(BUILTIN_DIR) in info.path or info.path.startswith(str(BUILTIN_DIR)):
                if info.name not in disabled_list:
                    self.enable(info.name)

        return plugins

    def load(self, name: str) -> Plugin | None:
        """Load a plugin by name

        Args:
            name: Plugin name

        Returns:
            Plugin instance if successful, None otherwise
        """
        info = self.plugin_registry.get(name)
        if info is None:
            return None

        if info.is_loaded:
            return info.instance

        plugin = self.loader.load(info)
        if plugin is None:
            return None

        # Call on_load lifecycle method
        try:
            plugin.on_load()
        except Exception as e:
            info.state = PluginState.ERROR
            info.error = f"on_load failed: {e}"
            return None

        return plugin

    def enable(self, name: str) -> bool:
        """Enable a plugin

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        info = self.plugin_registry.get(name)
        if info is None:
            return False

        # Load if not already loaded
        if not info.is_loaded:
            if self.load(name) is None:
                return False

        if info.state == PluginState.ENABLED:
            return True

        plugin = info.instance
        if plugin is None:
            return False

        try:
            # Call on_enable lifecycle method
            plugin.on_enable()

            # Register hooks
            self._register_hooks(name, plugin.get_hooks())

            # Register tools
            self._register_tools(name, plugin.get_tools())

            # Register commands
            self._register_commands(name, plugin.get_commands())

            info.state = PluginState.ENABLED
            return True

        except Exception as e:
            info.state = PluginState.ERROR
            info.error = f"on_enable failed: {e}"
            return False

    def disable(self, name: str) -> bool:
        """Disable a plugin

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        info = self.plugin_registry.get(name)
        if info is None:
            return False

        if info.state != PluginState.ENABLED:
            return True

        plugin = info.instance
        if plugin is None:
            return False

        try:
            # Call on_disable lifecycle method
            plugin.on_disable()

            # Unregister hooks
            self._unregister_hooks(name)

            # Unregister tools
            self._unregister_tools(name)

            # Unregister commands
            self._unregister_commands(name)

            info.state = PluginState.DISABLED
            return True

        except Exception as e:
            info.state = PluginState.ERROR
            info.error = f"on_disable failed: {e}"
            return False

    def unload(self, name: str) -> bool:
        """Unload a plugin

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        info = self.plugin_registry.get(name)
        if info is None:
            return False

        # Disable first if enabled
        if info.state == PluginState.ENABLED:
            if not self.disable(name):
                return False

        return self.loader.unload(info)

    def _register_hooks(self, plugin_name: str, hooks: list[Hook]) -> None:
        """Register hooks from a plugin"""
        hook_names = []

        for hook in hooks:
            self.hook_registry.register(hook)
            hook_names.append(hook.name)

        self._plugin_hooks[plugin_name] = hook_names

    def _unregister_hooks(self, plugin_name: str) -> None:
        """Unregister hooks from a plugin"""
        hook_names = self._plugin_hooks.pop(plugin_name, [])

        for name in hook_names:
            self.hook_registry.unregister(name)

    def _register_tools(self, plugin_name: str, tools: list["Tool"]) -> None:
        """Register tools from a plugin"""
        from ..tools.base import ToolRegistry

        tool_names = []

        for tool in tools:
            ToolRegistry.register(tool)
            tool_names.append(tool.name)

        self._plugin_tools[plugin_name] = tool_names

    def _unregister_tools(self, plugin_name: str) -> None:
        """Unregister tools from a plugin"""
        tool_names = self._plugin_tools.pop(plugin_name, [])
        for name in tool_names:
            ToolRegistry.unregister(name)

    def _register_commands(self, plugin_name: str, commands: list["Command"]) -> None:
        """Register commands from a plugin"""
        from ..cli.commands.base import CommandRegistry

        command_names = []

        for cmd in commands:
            CommandRegistry().register(cmd)
            command_names.append(cmd.name)

        self._plugin_commands[plugin_name] = command_names

    def _unregister_commands(self, plugin_name: str) -> None:
        """Unregister commands from a plugin"""
        from ..cli.commands.base import CommandRegistry

        command_names = self._plugin_commands.pop(plugin_name, [])

        registry = CommandRegistry()
        for name in command_names:
            registry.unregister(name)

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get plugin info by name"""
        return self.plugin_registry.get(name)

    def list_plugins(self) -> list[PluginInfo]:
        """List all discovered plugins"""
        return self.plugin_registry.all()

    def list_enabled(self) -> list[PluginInfo]:
        """List all enabled plugins"""
        return self.plugin_registry.enabled()

    def enable_all(self) -> dict[str, bool]:
        """Enable all discovered plugins

        Returns:
            Dict mapping plugin names to success status
        """
        results = {}

        for info in self.plugin_registry.all():
            if info.state != PluginState.ENABLED:
                results[info.name] = self.enable(info.name)

        return results

    def disable_all(self) -> dict[str, bool]:
        """Disable all enabled plugins

        Returns:
            Dict mapping plugin names to success status
        """
        results = {}

        for info in self.plugin_registry.enabled():
            results[info.name] = self.disable(info.name)

        return results
