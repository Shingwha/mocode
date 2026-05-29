"""AgentHook — class-based lifecycle hooks for AgentLoop.

Override methods on AgentHook to observe or modify agent loop state.
All methods receive a shared AgentHookContext. Modifier hooks (before_iteration,
after_tools) can mutate ctx.messages in place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import Response, ToolCall, Usage


@dataclass
class AgentHookContext:
    """Carries typed state through a single AgentLoop iteration."""
    # Response state (set before on_response)
    iteration: int = 0
    messages: list[dict] = field(default_factory=list)
    response: Response | None = None
    usage: Usage | None = None
    final_content: str = ""
    reasoning_content: str | None = None
    stop_reason: str | None = None
    error: Exception | None = None
    # Tool state
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    tool_result: str | None = None
    tool_error: str | None = None
    tool_timeout: int | None = None
    # Loop control
    continue_loop: bool = False
    # Compact state
    compact_old: int = 0
    compact_new: int = 0

    def reset_response(self) -> None:
        """Clear response-level fields before each iteration."""
        self.final_content = ""
        self.reasoning_content = None
        self.usage = None
        self.stop_reason = None
        self.tool_calls = []
        self.tool_results = []

    def reset_tool(self) -> None:
        """Clear tool-level fields before each tool call."""
        self.tool_name = ""
        self.tool_args = {}
        self.tool_call_id = ""
        self.tool_result = None
        self.tool_error = None
        self.tool_timeout = None


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

    _METHODS = frozenset({
        "before_iteration", "on_response", "after_tools",
        "after_iteration", "on_tool_start", "on_tool_complete", "on_compact",
    })

    def __init__(self, hooks: list[AgentHook] | None = None):
        self._hooks: list[AgentHook] = list(hooks or [])

    def add(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    async def _dispatch(self, method: str, ctx: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await getattr(h, method)(ctx)
            except Exception as e:
                logging.warning(f"Hook {h.__class__.__name__}.{method} failed: {e}")

    def __getattr__(self, name: str):
        if name in self._METHODS:
            async def method(ctx: AgentHookContext) -> None:
                await self._dispatch(name, ctx)
            return method
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
