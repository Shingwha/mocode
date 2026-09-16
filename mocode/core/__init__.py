"""MoCode core — the agent kernel.

Mechanism only: the loop, tools, hooks, prompt assembly and provider protocol.
Nothing in here knows about a specific feature — sub-agents, context compaction
or any other capability is a plugin built on these primitives.
"""

from __future__ import annotations

from .agent import AgentConfig, AgentLoop, LoopResult
from .builder import Agent
from .hook import (
    AgentHook,
    HookRunner,
    IterationContext,
    ToolCallContext,
    ToolTimingTracker,
)
from .prompt import Prompt, Section
from .provider import ModelSpec, Provider, Response, ToolCall, Usage, with_retry
from .tool import Tool, ToolError, ToolRegistry

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentHook",
    "AgentLoop",
    "HookRunner",
    "IterationContext",
    "LoopResult",
    "ModelSpec",
    "Prompt",
    "Provider",
    "Response",
    "Section",
    "Tool",
    "ToolCall",
    "ToolCallContext",
    "ToolError",
    "ToolRegistry",
    "ToolTimingTracker",
    "Usage",
    "with_retry",
]
