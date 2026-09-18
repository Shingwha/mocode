"""mocode.host — the layer an application embeds.

Config, sessions, the command contract, the plugin framework, and the runtime
that wires them together. It knows nothing about a terminal: a frontend is
anything satisfying :class:`~mocode.host.frontend.Frontend`, and the CLI shipped
alongside is just one implementation of it.

Embedding looks like this::

    from mocode import MoCode

    mc = MoCode()
    async for event in mc.chat("..."):
        ...

Going one level down — writing a plugin, or a frontend of your own — means
importing from here and from :mod:`mocode.core`.
"""

from __future__ import annotations

from .command import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from .config import Config, ModelEntry, ProviderEntry
from .frontend import Frontend
from .plugin import HostContext, Plugin, PluginHost, builtin_plugins
from .runtime import MoCode
from .session import Session, SessionManager, SessionStore

__all__ = [
    "CONTINUE",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "Config",
    "EXIT",
    "Frontend",
    "HostContext",
    "Kind",
    "MoCode",
    "ModelEntry",
    "Plugin",
    "PluginHost",
    "ProviderEntry",
    "Session",
    "SessionManager",
    "SessionStore",
    "builtin_plugins",
]
