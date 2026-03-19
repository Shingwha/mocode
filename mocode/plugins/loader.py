"""Plugin discovery and loading"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

from .base import Plugin, PluginInfo, PluginMetadata, PluginState
from ..paths import PLUGINS_DIR, PROJECT_SKILLS_DIRNAME


class PluginLoader:
    """Discovers and loads plugins from directories"""

    PLUGIN_FILE = "plugin.py"
    MANIFEST_FILE = "plugin.yaml"

    def __init__(self, plugins_dirs: list[Path] | None = None):
        """Initialize loader

        Args:
            plugins_dirs: List of directories to search for plugins.
                         If None, uses default directories.
        """
        if plugins_dirs is None:
            plugins_dirs = [
                PLUGINS_DIR,  # Global plugins
            ]
            # Add project-level plugins directory
            project_plugins = Path.cwd() / PROJECT_SKILLS_DIRNAME / "plugins"
            if project_plugins not in plugins_dirs:
                plugins_dirs.append(project_plugins)

        self.plugins_dirs = plugins_dirs

    def discover(self) -> list[PluginInfo]:
        """Discover all available plugins

        Returns:
            List of PluginInfo for discovered plugins
        """
        plugins: list[PluginInfo] = []

        for plugins_dir in self.plugins_dirs:
            if not plugins_dir.exists():
                continue

            for plugin_folder in plugins_dir.iterdir():
                if not plugin_folder.is_dir():
                    continue

                plugin_file = plugin_folder / self.PLUGIN_FILE
                if not plugin_file.exists():
                    continue

                info = self._create_plugin_info(plugin_folder)
                if info:
                    plugins.append(info)

        return plugins

    def _create_plugin_info(self, path: Path) -> PluginInfo | None:
        """Create PluginInfo from plugin directory"""
        plugin_name = path.name

        # Try to load metadata from plugin.yaml
        metadata = self._load_metadata(path)

        return PluginInfo(
            name=metadata.name if metadata else plugin_name,
            path=str(path),
            metadata=metadata,
            state=PluginState.DISCOVERED,
        )

    def _load_metadata(self, path: Path) -> PluginMetadata | None:
        """Load plugin metadata from plugin.yaml"""
        manifest_path = path / self.MANIFEST_FILE
        if not manifest_path.exists():
            return None

        try:
            content = manifest_path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if data and isinstance(data, dict):
                return PluginMetadata.from_dict(data)
        except Exception:
            pass

        return None

    def load(self, info: PluginInfo) -> Plugin | None:
        """Load a plugin from its directory

        Args:
            info: Plugin info for the plugin to load

        Returns:
            Plugin instance if successful, None otherwise
        """
        if info.state == PluginState.ENABLED:
            return info.instance

        plugin_path = Path(info.path)
        plugin_file = plugin_path / self.PLUGIN_FILE

        if not plugin_file.exists():
            info.state = PluginState.ERROR
            info.error = f"Plugin file not found: {plugin_file}"
            return None

        try:
            # Load the plugin module
            plugin_name = info.name
            module_name = f"mocode_plugin_{plugin_name}"

            spec = importlib.util.spec_from_file_location(
                module_name,
                plugin_file,
            )

            if spec is None or spec.loader is None:
                info.state = PluginState.ERROR
                info.error = f"Could not load spec from: {plugin_file}"
                return None

            # Create module
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module

            # Execute module
            spec.loader.exec_module(module)

            # Find Plugin subclass
            plugin_class = self._find_plugin_class(module)

            if plugin_class is None:
                info.state = PluginState.ERROR
                info.error = "No Plugin subclass found in plugin module"
                return None

            # Instantiate plugin
            instance = plugin_class()

            # Set metadata if not already set
            if not hasattr(instance, "metadata") or instance.metadata is None:
                instance.metadata = info.metadata or PluginMetadata(name=plugin_name)

            info.instance = instance
            info.state = PluginState.LOADED

            return instance

        except Exception as e:
            info.state = PluginState.ERROR
            info.error = str(e)
            return None

    def _find_plugin_class(self, module: Any) -> type[Plugin] | None:
        """Find Plugin subclass in module"""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            # Check if it's a class and subclass of Plugin
            if (
                isinstance(attr, type)
                and issubclass(attr, Plugin)
                and attr is not Plugin
            ):
                return attr

        return None

    def unload(self, info: PluginInfo) -> bool:
        """Unload a plugin

        Args:
            info: Plugin info for the plugin to unload

        Returns:
            True if successful
        """
        if info.instance is not None:
            try:
                info.instance.on_unload()
            except Exception:
                pass

        info.instance = None
        info.state = PluginState.DISCOVERED

        # Remove module from sys.modules
        module_name = f"mocode_plugin_{info.name}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        return True
