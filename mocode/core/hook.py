"""AgentHook — class-based lifecycle hooks for AgentLoop.

Override methods on AgentHook to observe or modify agent loop state.
All methods receive a shared AgentHookContext. Modifier hooks (before_iteration,
after_tools) can mutate ctx.messages in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import Response, ToolCall, Usage


@dataclass
class AgentHookContext:
    """Carries typed state through a single AgentLoop iteration."""
    # Iteration state
    iteration: int = 0
    messages: list[dict] = field(default_factory=list)
    response: Response | None = None
    usage: Usage | None = None
    final_content: str = ""
    stop_reason: str | None = None
    error: Exception | None = None
    # Batch tool state
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    # Per-tool state (set before on_tool_start / on_tool_complete)
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    tool_result: str | None = None
    tool_error: str | None = None
    tool_timeout: int | None = None
    # Loop control
    continue_loop: bool = False
    # Compact state (set before on_compact)
    compact_old: int = 0
    compact_new: int = 0


class AgentHook:
    """Base class for AgentLoop lifecycle hooks.

    Override any method to observe or modify agent loop state.
    All methods are no-ops by default.
    """

    async def before_iteration(self, ctx: AgentHookContext) -> None:
        """Before each LLM call. ctx.messages is modifiable."""

    async def on_response(self, ctx: AgentHookContext) -> None:
        """After each LLM response. Read ctx.response/final_content/usage."""

    async def after_tools(self, ctx: AgentHookContext) -> None:
        """After all tool results are collected. ctx.messages is modifiable."""

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        """After each loop iteration completes."""

    async def on_tool_start(self, ctx: AgentHookContext) -> None:
        """Before a single tool executes. Read ctx.tool_name/tool_args/tool_call_id."""

    async def on_tool_complete(self, ctx: AgentHookContext) -> None:
        """After a single tool completes. Read ctx.tool_result/tool_error/tool_timeout."""

    async def on_compact(self, ctx: AgentHookContext) -> None:
        """When context is compacted. Read ctx.compact_old/compact_new."""


class HookRunner:
    """Fan-out dispatcher for a list of AgentHooks with error isolation."""

    def __init__(self, hooks: list[AgentHook] | None = None):
        self._hooks: list[AgentHook] = list(hooks or [])

    def add(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    async def before_iteration(self, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.before_iteration(ctx)
            except Exception:
                pass

    async def after_tools(self, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.after_tools(ctx)
            except Exception:
                pass

    async def on_response(self, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.on_response(ctx)
            except Exception:
                pass

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.after_iteration(ctx)
            except Exception:
                pass

    async def on_tool_start(self, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.on_tool_start(ctx)
            except Exception:
                pass

    async def on_tool_complete(self, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.on_tool_complete(ctx)
            except Exception:
                pass

    async def on_compact(self, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.on_compact(ctx)
            except Exception:
                pass
