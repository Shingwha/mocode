"""Plugin Coordinator - Coordinates plugin management with config persistence"""

from typing import TYPE_CHECKING, Callable

from ..plugins import PluginInfo, PluginManager

if TYPE_CHECKING:
    from .config import Config


class PluginCoordinator:
    """Coordinates plugin management with config persistence

    Ensures that plugin enable/disable operations are reflected
    in the configuration and persisted when needed.
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        config: "Config",
        on_change: Callable[[], None] | None = None,
    ):
        """Initialize plugin coordinator

        Args:
            plugin_manager: Plugin manager instance
            config: Configuration to sync with
            on_change: Optional callback when config changes (for persistence)
        """
        self._plugin_manager = plugin_manager
        self._config = config
        self._on_change = on_change

    def initialize(self) -> list[PluginInfo]:
        """Initialize plugins from config

        Discovers plugins, auto-enables builtins, and enables
        plugins marked as enabled in config.

        Returns:
            List of discovered plugins
        """
        # Get disabled list from config
        disabled_list = [
            name for name, state in self._config.plugins.items()
            if state == "disable"
        ]

        # Discover and auto-enable builtins
        plugins = self._plugin_manager.discover_and_enable_builtins(
            disabled_list=disabled_list
        )

        # Enable plugins marked as enable in config
        for plugin_name, state in self._config.plugins.items():
            if state == "enable":
                self._plugin_manager.enable(plugin_name)

        return plugins

    def enable_plugin(self, name: str) -> bool:
        """Enable a plugin

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        success = self._plugin_manager.enable(name)
        if success:
            # Update config
            self._config.plugins[name] = "enable"

            # Trigger persistence
            if self._on_change:
                self._on_change()

        return success

    def disable_plugin(self, name: str) -> bool:
        """Disable a plugin

        Args:
            name: Plugin name

        Returns:
            True if successful
        """
        success = self._plugin_manager.disable(name)
        if success:
            # Update config
            self._config.plugins[name] = "disable"

            # Trigger persistence
            if self._on_change:
                self._on_change()

        return success

    def list_plugins(self) -> list[PluginInfo]:
        """List all discovered plugins

        Returns:
            List of plugin info objects
        """
        return self._plugin_manager.list_plugins()

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get plugin info by name

        Args:
            name: Plugin name

        Returns:
            Plugin info if found, None otherwise
        """
        return self._plugin_manager.get_plugin_info(name)

    @property
    def plugin_manager(self) -> PluginManager:
        """Underlying plugin manager"""
        return self._plugin_manager
