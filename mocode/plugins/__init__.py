"""MoCode plugin SDK — the surface a plugin author imports.

A plugin lives in ``./.mocode/plugins/<name>/`` (or ``~/.mocode/plugins/``) as a
``PLUGIN.md`` plus a ``plugin.py``::

    from mocode.plugins import Plugin, Tool

    class GreetTool(Tool):
        def __init__(self):
            super().__init__(
                name="greet",
                description="Greet someone by name",
                params={"who": {"type": "string", "description": "Name to greet"}},
                func=self._run,
                tags=frozenset({"demo"}),
            )

        def _run(self, args: dict) -> str:
            return f"Hello, {args['who']}!"

    class GreetPlugin(Plugin):
        name = "greet"
        description = "A greeting tool"

        def build(self, ctx):
            ctx.tools.register(GreetTool())

Plugins are trusted code — installing one means running it.
"""

from __future__ import annotations

from ..app.cli.commands import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandResult,
    Kind,
)
from ..app.plugin.base import Plugin
from ..app.plugin.context import HostContext
from ..core.hook import (
    AgentHook,
    HookRunner,
    IterationContext,
    ToolCallContext,
    ToolTimingTracker,
)
from ..core.prompt import Prompt, Section
from ..core.tool import Tool, ToolError, ToolRegistry

__all__ = [
    "AgentHook",
    "CONTINUE",
    "Command",
    "CommandContext",
    "CommandResult",
    "EXIT",
    "HookRunner",
    "HostContext",
    "IterationContext",
    "Kind",
    "Plugin",
    "Prompt",
    "Section",
    "Tool",
    "ToolCallContext",
    "ToolError",
    "ToolRegistry",
    "ToolTimingTracker",
]
