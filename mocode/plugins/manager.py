"""Plugin Manager - Lifecycle management"""

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .base import Hook, Plugin, PluginInfo, PluginState
from .context import PluginContext
from .loader import BUILTIN_DIR, PluginLoader
from .registry import HookRegistry, PluginRegistry
from .venv_manager import PluginVenvManager, VenvError

if TYPE_CHECKING:
    from ..tools.base import Tool
    from ..cli.commands.base import Command
    from ..core.prompt.builder import PromptBuilder, PromptSection

# Type alias for tool replacement tracking: (plugin_name, original_tool)
ToolReplacement = tuple[str, "Tool"]


class PluginManager:
    """Manages plugin lifecycle and integration"""

    def __init__(
        self,
        hook_registry: HookRegistry | None = None,
        plugin_registry: PluginRegistry | None = None,
        loader: PluginLoader | None = None,
        create_plugin_context: Callable[[], PluginContext] | None = None,
        prompt_builder: "PromptBuilder | None" = None,
    ):
        """Initialize plugin manager

        Args:
            hook_registry: Hook registry instance (creates new if None)
            plugin_registry: Plugin registry instance (creates new if None)
            loader: Plugin loader instance (creates new if None)
            create_plugin_context: Callback to create PluginContext for plugins
            prompt_builder: PromptBuilder for registering plugin prompt sections
        """
        self.hook_registry = hook_registry or HookRegistry()
        self.plugin_registry = plugin_registry or PluginRegistry()
        self.loader = loader or PluginLoader()
        self._create_plugin_context = create_plugin_context
        self._prompt_builder = prompt_builder

        # Track registered hooks/tools/commands/sections by plugin
        self._plugin_hooks: dict[str, list[str]] = {}
        self._plugin_tools: dict[str, list[str]] = {}
        self._plugin_commands: dict[str, list[str]] = {}
        self._plugin_sections: dict[str, list[str]] = {}

        # Track tool replacements: {tool_name: [(plugin_name, original_tool), ...]}
        self._tool_replacements: dict[str, list[ToolReplacement]] = {}

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

        For builtins, lifecycle methods are trivial no-ops, so we skip
        async lifecycle handling. Use async enable() for runtime operations.

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
                    self._enable_sync(info)

        return plugins

    def _enable_sync(self, info: PluginInfo) -> bool:
        """Enable a plugin synchronously (for initialization only).

        Skips async lifecycle methods. Use async enable() for runtime operations
        that need proper lifecycle handling.
        """
        # Load if not already loaded
        if not info.is_loaded:
            plugin = self.loader.load(info)
            if plugin is None:
                return False

        if info.state == PluginState.ENABLED:
            return True

        plugin = info.instance
        if plugin is None:
            return False

        try:
            # Inject PluginContext
            if self._create_plugin_context:
                context = self._create_plugin_context()
                plugin.set_context(context)

            # Register hooks
            self._register_hooks(info.name, plugin.get_hooks())

            # Register tools
            self._register_tools(info.name, plugin.get_tools())

            # Register commands
            self._register_commands(info.name, plugin.get_commands())

            # Register prompt sections
            self._register_prompt_sections(info.name, plugin.get_prompt_sections())

            info.state = PluginState.ENABLED
            return True

        except Exception as e:
            info.state = PluginState.ERROR
            info.error = f"enable_sync failed: {e}"
            return False

    async def load(self, name: str) -> Plugin | None:
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
            await plugin.on_load()
        except Exception as e:
            info.state = PluginState.ERROR
            info.error = f"on_load failed: {e}"
            return None

        return plugin

    async def enable(self, name: str) -> bool:
        """Enable a plugin

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        info = self.plugin_registry.get(name)
        if info is None:
            return False

        # Set up isolated venv for plugins with dependencies (skip builtin)
        plugin_path = Path(info.path)
        if not str(plugin_path).startswith(str(BUILTIN_DIR)):
            if info.metadata and info.metadata.dependencies:
                try:
                    venv_manager = PluginVenvManager(plugin_path)
                    if not venv_manager.exists:
                        venv_manager.create()
                    venv_manager.install_dependencies(info.metadata.dependencies)
                except VenvError as e:
                    info.state = PluginState.ERROR
                    info.error = f"Failed to set up plugin environment: {e}"
                    return False

        # Load if not already loaded
        if not info.is_loaded:
            if await self.load(name) is None:
                return False

        if info.state == PluginState.ENABLED:
            return True

        plugin = info.instance
        if plugin is None:
            return False

        try:
            # Call on_enable lifecycle method
            await plugin.on_enable()

            # Inject PluginContext after on_enable
            if self._create_plugin_context:
                context = self._create_plugin_context()
                plugin.set_context(context)

            # Register hooks
            self._register_hooks(name, plugin.get_hooks())

            # Register tools
            self._register_tools(name, plugin.get_tools())

            # Register commands
            self._register_commands(name, plugin.get_commands())

            # Register prompt sections
            self._register_prompt_sections(name, plugin.get_prompt_sections())

            info.state = PluginState.ENABLED
            return True

        except Exception as e:
            info.state = PluginState.ERROR
            info.error = f"on_enable failed: {e}"
            return False

    async def disable(self, name: str) -> bool:
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
            await plugin.on_disable()

            # Unregister hooks
            self._unregister_hooks(name)

            # Unregister tools
            self._unregister_tools(name)

            # Unregister commands
            self._unregister_commands(name)

            # Unregister prompt sections
            self._unregister_prompt_sections(name)

            info.state = PluginState.DISABLED
            return True

        except Exception as e:
            info.state = PluginState.ERROR
            info.error = f"on_disable failed: {e}"
            return False

    async def unload(self, name: str) -> bool:
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
            if not await self.disable(name):
                return False

        return await self.loader.unload_async(info)

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
        """Register tools from a plugin and save replaced tools for restoration"""
        from ..tools.base import ToolRegistry

        tool_names = []

        for tool in tools:
            # Save original tool if this is a replacement
            original = ToolRegistry.get(tool.name)
            if original is not None:
                if tool.name not in self._tool_replacements:
                    self._tool_replacements[tool.name] = []
                self._tool_replacements[tool.name].append((plugin_name, original))

            ToolRegistry.register(tool)
            tool_names.append(tool.name)

        self._plugin_tools[plugin_name] = tool_names

    def _unregister_tools(self, plugin_name: str) -> None:
        """Unregister tools from a plugin and restore replaced tools"""
        from ..tools.base import ToolRegistry

        tool_names = self._plugin_tools.pop(plugin_name, [])

        for name in tool_names:
            if name in self._tool_replacements:
                replacements = self._tool_replacements[name]

                # Find this plugin's replacement record
                found_idx = None
                for i, (repl_plugin, _) in enumerate(replacements):
                    if repl_plugin == plugin_name:
                        found_idx = i
                        break

                if found_idx is not None:
                    # Get the tool that was saved when this plugin registered
                    _, saved_tool = replacements.pop(found_idx)

                    if not replacements:
                        # No more replacements, restore the saved tool
                        del self._tool_replacements[name]
                        ToolRegistry.register(saved_tool)
                    elif found_idx == len(replacements):
                        # Was top of stack, restore the saved tool
                        ToolRegistry.register(saved_tool)
                    # else: Not top of stack, current tool unchanged
                else:
                    # No replacement record, just unregister
                    ToolRegistry.unregister(name)
            else:
                # No replacement record, just unregister
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

    def _register_prompt_sections(self, plugin_name: str, contributions: "PromptContributions") -> None:
        """Register prompt contributions from a plugin (add/disable/replace)"""
        if not self._prompt_builder:
            return

        from ..core.prompt.builder import PromptContributions

        if not contributions:
            return

        added_names: list[str] = []
        replaced: dict[str, tuple] = {}  # orig_name -> (original_section, replacement_section)

        # Add new sections
        for section in contributions.add:
            self._prompt_builder.add(section)
            added_names.append(section.name)

        # Disable existing sections
        for name in contributions.disable:
            self._prompt_builder.disable(name)

        # Replace existing sections (save originals for restoration)
        for name, replacement in contributions.replace.items():
            original = self._prompt_builder.get_section(name)
            if original is not None:
                replaced[name] = (original, replacement)
                self._prompt_builder.remove(name)
            self._prompt_builder.add(replacement)

        self._plugin_sections[plugin_name] = {
            "added": added_names,
            "disabled": list(contributions.disable),
            "replaced": replaced,
        }

    def _unregister_prompt_sections(self, plugin_name: str) -> None:
        """Unregister prompt contributions from a plugin (restore original state)"""
        state = self._plugin_sections.pop(plugin_name, None)
        if not state or not self._prompt_builder:
            return

        # Remove added sections
        for name in state["added"]:
            self._prompt_builder.remove(name)

        # Re-enable disabled sections
        for name in state["disabled"]:
            self._prompt_builder.enable(name)

        # Restore replaced sections
        for _orig_name, (original, replacement) in state["replaced"].items():
            self._prompt_builder.remove(replacement.name)
            self._prompt_builder.add(original)

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get plugin info by name"""
        return self.plugin_registry.get(name)

    def list_plugins(self) -> list[PluginInfo]:
        """List all discovered plugins"""
        return self.plugin_registry.all()

    def list_enabled(self) -> list[PluginInfo]:
        """List all enabled plugins"""
        return self.plugin_registry.enabled()

    async def enable_all(self) -> dict[str, bool]:
        """Enable all discovered plugins

        Returns:
            Dict mapping plugin names to success status
        """
        results = {}

        for info in self.plugin_registry.all():
            if info.state != PluginState.ENABLED:
                results[info.name] = await self.enable(info.name)

        return results

    async def disable_all(self) -> dict[str, bool]:
        """Disable all enabled plugins

        Returns:
            Dict mapping plugin names to success status
        """
        results = {}

        for info in self.plugin_registry.enabled():
            results[info.name] = await self.disable(info.name)

        return results
