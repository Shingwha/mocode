"""mocode.host — the layer an application embeds.

Config, sessions, the command contract, the plugin framework, and the runtime
that wires them together. It knows nothing about a user interface: a conversation
is one project, one model and one event stream, and whatever draws it — a
terminal, a browser, a test — is a subscriber.

Embedding looks like this::

    from mocode import MoCode

    mc = MoCode()
    conv = mc.new_conversation(cwd="/srv/proj-a")
    async for event in conv.chat("..."):
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
    dispatch,
)
from .config import Config, ModelEntry, ProviderEntry
from .conversation import Conversation
from .events import ConversationChanged
from .plugin import HostContext, Plugin, PluginHost, builtin_plugins, load_plugins
from .runtime import MoCode
from .session import (
    Session,
    SessionStore,
    export_session,
    export_session_md,
    load_session_file,
)

__all__ = [
    "CONTINUE",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "Config",
    "Conversation",
    "ConversationChanged",
    "EXIT",
    "HostContext",
    "Kind",
    "MoCode",
    "ModelEntry",
    "Plugin",
    "PluginHost",
    "ProviderEntry",
    "Session",
    "SessionStore",
    "builtin_plugins",
    "dispatch",
    "export_session",
    "export_session_md",
    "load_plugins",
    "load_session_file",
]
