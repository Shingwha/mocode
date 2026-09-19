"""MoCode plugin framework — the extension contract and its host."""

from .base import Plugin
from .context import HostContext
from .host import (
    LoadedPlugins,
    PluginHost,
    builtin_plugins,
    default_plugin_dirs,
    load_plugins,
)
from .loader import (
    HOST_NAMESPACE,
    MANIFEST,
    PluginSpec,
    discover,
    load_plugin,
    namespace_dir,
    read_manifest,
)

__all__ = [
    "HOST_NAMESPACE",
    "MANIFEST",
    "HostContext",
    "LoadedPlugins",
    "Plugin",
    "PluginHost",
    "PluginSpec",
    "builtin_plugins",
    "default_plugin_dirs",
    "discover",
    "load_plugin",
    "load_plugins",
    "namespace_dir",
    "read_manifest",
]
