"""MoCode plugin SDK — the surface a plugin author imports.

A plugin is a directory following the Agent Plugins layout, installed in
``./.mocode/plugins/`` (project) or ``~/.mocode/plugins/`` (user)::

    greet/
    ├── plugin.json          name, version, description
    ├── skills/<n>/SKILL.md  portable skills (optional)
    └── mocode/plugin.py     MoCode's namespace: the code below

Portable parts are data any compatible client can read; MoCode's code lives in
its own namespace, which other clients ignore::

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

A single ``greet.py`` file next to the plugin directories works too, for a
plugin with no portable parts. Contribution to a *frontend* rather than to the
agent has its own namespace (``mocode.cli/plugin.py``) and its own interface —
see :class:`mocode.cli.CLIPlugin`.

Plugins are trusted code — installing one means running it.
"""

from __future__ import annotations

from ..core.agent import AgentConfig, LoopResult, Turn
from ..core.channel import EventChannel, Subscription
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
    TOOL_DENIED,
    TOOL_ERROR,
    TOOL_NOT_FOUND,
    TOOL_OK,
    TOOL_TIMEOUT,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
    ToolStatus,
)
from ..core.hook import AgentHook, HookRunner, IterationContext, ToolCallContext
from ..core.prompt import Prompt, Section
from ..core.provider import (
    Chunk,
    ModelSpec,
    Provider,
    StreamAccumulator,
    ToolCall,
    ToolCallDelta,
    Usage,
)
from ..core.state import RunState
from ..core.tool import Tool, ToolError, ToolRegistry, ToolResult
from ..host.command import (
    CONTINUE,
    EXIT,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from ..host.conversation import Conversation
from ..host.plugin.base import Plugin
from ..host.plugin.context import HostContext

__all__ = [
    "AgentConfig",
    "AgentHook",
    "Chunk",
    "CONTINUE",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "Conversation",
    "EXIT",
    "Event",
    "EventChannel",
    "HookRunner",
    "HostContext",
    "IterationContext",
    "IterationFinished",
    "IterationStarted",
    "Kind",
    "LoopResult",
    "ModelSpec",
    "Notice",
    "Plugin",
    "Prompt",
    "Provider",
    "ReasoningDelta",
    "RunFailed",
    "RunFinished",
    "RunStarted",
    "RunState",
    "Section",
    "StreamAccumulator",
    "Subscription",
    "TextDelta",
    "TOOL_DENIED",
    "TOOL_ERROR",
    "TOOL_NOT_FOUND",
    "TOOL_OK",
    "TOOL_TIMEOUT",
    "Tool",
    "ToolCall",
    "ToolCallContext",
    "ToolCallDelta",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolError",
    "ToolOutput",
    "ToolRegistry",
    "ToolResult",
    "ToolStatus",
    "Turn",
    "Usage",
]
