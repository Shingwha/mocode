"""MoCode plugin SDK — the surface a plugin author imports.

A plugin is a directory following the Agent Plugins layout, installed in
``./.mocode/plugins/`` (project) or ``~/.mocode/plugins/`` (user)::

    greet/
    ├── plugin.json          name, version, description
    ├── skills/<n>/SKILL.md  portable skills (optional)
    └── mocode/plugin.py     MoCode's namespace: the code below

Portable parts are data any compatible client can read; MoCode's code lives in
its own namespace, which other clients ignore. The whole of a small plugin::

    from mocode.plugins import Plugin, Section, Tool

    def _greet(args: dict) -> str:
        return f"Hello, {args['who']}!"

    class GreetPlugin(Plugin):
        name = "greet"
        description = "A greeting tool"

        def build(self, ctx):
            ctx.tools.register(Tool(
                name="greet",
                description="Greet someone by name",
                schema={
                    "type": "object",
                    "properties": {
                        "who": {"type": "string", "description": "Name to greet"},
                    },
                    "required": ["who"],
                },
                func=_greet,
                tags=frozenset({"demo"}),
            ))
            ctx.prompt_sections.append(Section("greet", "Be friendly.", priority=45))

``build()`` receives a :class:`BuildContext` — contribution targets, config,
paths, no agent. ``prepare()`` and ``close()`` receive a :class:`HostContext`:
the same object once the loop exists, carrying the agent, ``emit`` /
``emit_message`` / ``subscribe`` and ``spawn``. A tool that wants its call
context declares ``with_context=True`` and receives ``(args, ctx)``.

A single ``greet.py`` file next to the plugin directories works too, for a
plugin with no portable parts; a larger one becomes a package
(``mocode/plugin/__init__.py``). Contribution to a *frontend* rather than to
the agent has its own namespace (``mocode.cli/plugin.py``) and its own
interface — see :class:`mocode.cli.CLIPlugin`.

Plugins are trusted code — installing one means running it.
"""

from __future__ import annotations

from ..core.agent import AgentConfig, IterationLimit, LoopResult, Turn
from ..core.channel import EventChannel, Subscription
from ..core.dispatch import DispatchResult, ToolDispatcher
from ..core.events import (
    Event,
    IterationFinished,
    IterationStarted,
    Notice,
    PluginMessage,
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
    ToolCallArgsDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
    ToolStatus,
)
from ..core.hook import (
    AgentHook,
    HookRunner,
    IterationContext,
    RequestContext,
    ResponseContext,
    ToolCallContext,
)
from ..core.prompt import Prompt, Section
from ..core.provider import (
    Chunk,
    EFFORTS,
    Effort,
    ModelSpec,
    Provider,
    RetryDeadlineExceeded,
    RetryPolicy,
    StreamAccumulator,
    ToolCall,
    ToolCallDelta,
    Usage,
)
from ..core.state import RunState
from ..core.tool import (
    Tool,
    ToolConflictError,
    ToolError,
    ToolPolicy,
    ToolRegistry,
    ToolResult,
)
from ..core.transcript import (
    IMAGE_PLACEHOLDER,
    answered_call_id,
    assistant_message,
    content_parts,
    is_assistant,
    is_tool_result,
    is_user,
    reasoning_of,
    text_of,
    tool_call_args,
    tool_call_arguments,
    tool_call_by_id,
    tool_call_dicts,
    tool_call_id,
    tool_call_name,
    tool_calls_of,
    tool_result,
)
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
from ..host.plugin.context import BuildContext, HostContext

__all__ = [
    "AgentConfig",
    "AgentHook",
    "BuildContext",
    "Chunk",
    "CONTINUE",
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "Conversation",
    "DispatchResult",
    "EFFORTS",
    "Effort",
    "EXIT",
    "Event",
    "EventChannel",
    "HookRunner",
    "HostContext",
    "IMAGE_PLACEHOLDER",
    "IterationContext",
    "IterationFinished",
    "IterationLimit",
    "IterationStarted",
    "Kind",
    "LoopResult",
    "ModelSpec",
    "Notice",
    "Plugin",
    "PluginMessage",
    "Prompt",
    "Provider",
    "ReasoningDelta",
    "RequestContext",
    "ResponseContext",
    "RetryDeadlineExceeded",
    "RetryPolicy",
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
    "ToolCallArgsDelta",
    "ToolCallContext",
    "ToolCallDelta",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolConflictError",
    "ToolDispatcher",
    "ToolError",
    "ToolOutput",
    "ToolPolicy",
    "ToolRegistry",
    "ToolResult",
    "ToolStatus",
    "Turn",
    "Usage",
    "answered_call_id",
    "assistant_message",
    "content_parts",
    "is_assistant",
    "is_tool_result",
    "is_user",
    "reasoning_of",
    "text_of",
    "tool_call_args",
    "tool_call_arguments",
    "tool_call_by_id",
    "tool_call_dicts",
    "tool_call_id",
    "tool_call_name",
    "tool_calls_of",
    "tool_result",
]
