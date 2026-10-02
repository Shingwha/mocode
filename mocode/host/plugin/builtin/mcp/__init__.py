"""mcp builtin plugin — connect to MCP servers over stdio and expose their tools.

The package is split by responsibility — :mod:`.config` where server
entries come from and what they mean, :mod:`.naming` how servers and tools
are named and who sees them, :mod:`.rpc` the wire framing,
:mod:`.session` one stdio connection, :mod:`.runtime` the per-conversation
state, :mod:`.tools` the tool builders, :mod:`.plugin` the lifecycle — and
re-exports the names a test or a host needs, so
``from ...builtin.mcp import PLUGIN, McpPlugin, McpRuntime`` keeps working.
"""

from __future__ import annotations

from .plugin import PLUGIN, McpPlugin
from .runtime import McpRuntime

__all__ = ["PLUGIN", "McpPlugin", "McpRuntime"]
