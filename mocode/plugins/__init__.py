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

from ..core.events import (
    Event,
    IterationFinished,
    IterationStarted,
    Notice,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
)
from ..core.hook import AgentHook, HookRunner, IterationContext, ToolCallContext
from ..core.prompt import Prompt, Section
from ..core.tool import Tool, ToolError, ToolRegistry, ToolResult
from ..host.command import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandResult,
    Kind,
)
from ..host.frontend import Frontend
from ..host.plugin.base import Plugin
from ..host.plugin.context import HostContext

__all__ = [
    "AgentHook",
    "CONTINUE",
    "Command",
    "CommandContext",
    "CommandResult",
    "EXIT",
    "Event",
    "Frontend",
    "HookRunner",
    "HostContext",
    "IterationContext",
    "IterationFinished",
    "IterationStarted",
    "Kind",
    "Notice",
    "Plugin",
    "Prompt",
    "ReasoningDelta",
    "RunFailed",
    "RunFinished",
    "RunStarted",
    "Section",
    "TextDelta",
    "Tool",
    "ToolCallContext",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolError",
    "ToolOutput",
    "ToolRegistry",
    "ToolResult",
]
