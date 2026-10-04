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
* :class:`ToolDispatcher` — the one execution path for a tool call, shared by
  the loop (model origin) and by code driving tools of its own (program
  origin); see :mod:`mocode.core.dispatch` for the contract.

The :class:`AgentHook` methods are the interception channel (rewrite messages,
veto a tool call); ``on_event`` is the in-band way to watch, which the channel
awaits. Everything else watches out-of-band and never slows the run down.
"""

from __future__ import annotations

from .agent import AgentConfig, AgentLoop, IterationLimit, LoopResult
from .channel import EventChannel, Subscription
from .dispatch import DispatchResult, ToolDispatcher
from .events import (
    Event,
    IterationFinished,
    IterationStarted,
    Notice,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    StopReason,
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
from .hook import (
    AgentHook,
    HookRunner,
    IterationContext,
    RequestContext,
    ResponseContext,
    ToolCallContext,
)
from .prompt import Prompt, Section
from .provider import (
    Chunk,
    EFFORTS,
    Effort,
    ModelSpec,
    Provider,
    Response,
    RetryDeadlineExceeded,
    RetryPolicy,
    StreamAccumulator,
    ToolCall,
    ToolCallDelta,
    Usage,
    with_retry_stream,
)
from .state import RunState, ToolCallState
from .tool import (
    Tool,
    ToolConflictError,
    ToolError,
    ToolPolicy,
    ToolRegistry,
    ToolResult,
    split_result,
)
from .transcript import (
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
from .turn import Turn

__all__ = [
    "AgentConfig",
    "AgentHook",
    "AgentLoop",
    "Chunk",
    "DispatchResult",
    "EFFORTS",
    "Effort",
    "Event",
    "EventChannel",
    "HookRunner",
    "IMAGE_PLACEHOLDER",
    "IterationContext",
    "IterationFinished",
    "IterationLimit",
    "IterationStarted",
    "LoopResult",
    "ModelSpec",
    "Notice",
    "Prompt",
    "Provider",
    "ReasoningDelta",
    "RequestContext",
    "Response",
    "ResponseContext",
    "RetryDeadlineExceeded",
    "RetryPolicy",
    "RunFailed",
    "RunFinished",
    "RunStarted",
    "RunState",
    "StopReason",
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
    "ToolCallState",
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
    "split_result",
    "text_of",
    "tool_call_args",
    "tool_call_arguments",
    "tool_call_by_id",
    "tool_call_dicts",
    "tool_call_id",
    "tool_call_name",
    "tool_calls_of",
    "tool_result",
    "with_retry_stream",
]
