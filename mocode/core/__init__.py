"""MoCode core — the agent kernel.

Mechanism only: the loop, its event stream, tools, hooks, prompt assembly and
the provider protocol. Nothing in here knows about a specific feature —
sub-agents, context compaction or any other capability is a plugin built on
these primitives.

The two contract types to know:

* :class:`AgentLoop` — ``stream()`` is the only execution entry point; it
  yields :class:`Event` objects and never prints anything.
* :class:`RunState` — the same events folded into a live snapshot, for callers
  that want to ask "what is happening now" instead of watching the stream.

The :class:`AgentHook` methods are the interception channel (rewrite messages,
veto a tool call); they are deliberately separate from the event stream, which
is one-way.
"""

from __future__ import annotations

from .agent import AgentConfig, AgentLoop, LoopResult
from .builder import Agent
from .events import (
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
from .hook import AgentHook, HookRunner, IterationContext, ToolCallContext
from .prompt import Prompt, Section
from .provider import (
    Chunk,
    ModelSpec,
    Provider,
    Response,
    StreamAccumulator,
    ToolCall,
    ToolCallDelta,
    Usage,
    with_retry_stream,
)
from .state import RunState, ToolCallState
from .tool import Tool, ToolError, ToolRegistry, ToolResult, split_result

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentHook",
    "AgentLoop",
    "Chunk",
    "Event",
    "HookRunner",
    "IterationContext",
    "IterationFinished",
    "IterationStarted",
    "LoopResult",
    "ModelSpec",
    "Notice",
    "Prompt",
    "Provider",
    "ReasoningDelta",
    "Response",
    "RunFailed",
    "RunFinished",
    "RunStarted",
    "RunState",
    "Section",
    "StreamAccumulator",
    "TextDelta",
    "Tool",
    "ToolCall",
    "ToolCallContext",
    "ToolCallDelta",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolCallState",
    "ToolError",
    "ToolOutput",
    "ToolRegistry",
    "ToolResult",
    "Usage",
    "split_result",
    "with_retry_stream",
]
