"""MoCode core — the agent kernel.

Mechanism only: the loop, its event stream, tools, hooks, prompt assembly and
the provider protocol. Nothing in here knows about a specific feature —
sub-agents, context compaction or any other capability is a plugin built on
these primitives.

The contract types to know:

* :class:`AgentLoop` — ``start()`` is the only way to execute a turn; it returns
  a :class:`Turn`, and ``stream()`` / ``chat()`` are views over it.
* :class:`EventChannel` — where a turn's events go. One channel per
  conversation, every reader a subscription: a renderer, a status page, a
  reconnecting transport.
* :class:`RunState` — the same events folded into a live snapshot, for callers
  that want to ask "what is happening now" instead of watching the stream.

The :class:`AgentHook` methods are the interception channel (rewrite messages,
veto a tool call); ``on_event`` is the in-band way to watch, which the channel
awaits. Everything else watches out-of-band and never slows the run down.
"""

from __future__ import annotations

from .agent import AgentConfig, AgentLoop, LoopResult
from .channel import EventChannel, Subscription
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
from .turn import Turn

__all__ = [
    "AgentConfig",
    "AgentHook",
    "AgentLoop",
    "Chunk",
    "Event",
    "EventChannel",
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
    "Subscription",
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
    "Turn",
    "Usage",
    "split_result",
    "with_retry_stream",
]
