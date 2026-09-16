"""MoCode plugin framework — the extension contract and its host."""

from .base import Plugin
from .context import HostContext
from .host import PluginHost, builtin_plugins
from .loader import PluginSpec, discover, load_plugin, parse_frontmatter

__all__ = [
    "HostContext",
    "Plugin",
    "PluginHost",
    "PluginSpec",
    "builtin_plugins",
    "discover",
    "load_plugin",
    "parse_frontmatter",
]
