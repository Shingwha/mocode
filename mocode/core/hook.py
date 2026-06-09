"""AgentHook — class-based lifecycle hooks for AgentLoop.

Override methods on AgentHook to observe or modify agent loop state.
Methods receive typed context objects:
  - IterationContext  (before_iteration / on_response / after_tools / after_iteration)
  - ToolCallContext   (on_tool_start / on_tool_complete)
  - CompactContext    (on_compact)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import Response, ToolCall, Usage


@dataclass
class IterationContext:
    """before_iteration / on_response / after_tools / after_iteration"""

    messages: list[dict] = field(default_factory=list)
    iteration: int = 0
    response: Response | None = None
    usage: Usage | None = None
    final_content: str = ""
    reasoning_content: str | None = None
    stop_reason: str | None = None
    error: Exception | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    _needs_compact: bool = False


@dataclass
class ToolCallContext:
    """on_tool_start / on_tool_complete — one independent instance per concurrent tool call"""

    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    tool_result: str | None = None
    tool_error: str | None = None
    tool_timeout: int | None = None


@dataclass
class CompactContext:
    """on_compact"""

    messages: list[dict] = field(default_factory=list)  # mutable reference, hook modifies in place
    old_count: int = 0
    new_count: int = 0


@dataclass
class ToolTimingTracker:
    """Reusable tracker for per-call timing and per-tool-name error/elapsed tracking.

    Provides start(call_id) / complete(call_id, name, error, timeout) / reset()
    so that multiple Hook classes can share the same timing logic via composition.
    """

    _call_start: dict[str, float] = field(default_factory=dict)
    _elapsed: dict[str, float] = field(default_factory=dict)
    _errors: dict[str, str] = field(default_factory=dict)

    @property
    def elapsed(self) -> dict[str, float]:
        """Max elapsed seconds keyed by tool name."""
        return self._elapsed

    @property
    def errors(self) -> dict[str, str]:
        """First error message keyed by tool name."""
        return self._errors

    def start(self, call_id: str) -> None:
        """Record monotonic start time for *call_id*."""
        self._call_start[call_id] = time.monotonic()

    def complete(
        self,
        call_id: str,
        name: str,
        error: str | None = None,
        timeout: int | None = None,
    ) -> float:
        """Finalise *call_id*, return elapsed seconds.

        Tracks max elapsed per *name* and first error/timeout per *name*.
        """
        elapsed = 0.0
        if call_id in self._call_start:
            elapsed = time.monotonic() - self._call_start[call_id]
            prev = self._elapsed.get(name, 0.0)
            self._elapsed[name] = max(prev, elapsed)
        if timeout is not None:
            self._errors.setdefault(name, f"timeout {timeout}s")
        elif error:
            self._errors.setdefault(name, error[:80])
        return elapsed

    def reset(self) -> None:
        """Clear all tracking state."""
        self._call_start.clear()
        self._elapsed.clear()
        self._errors.clear()


class AgentHook:
    """Base class for AgentLoop lifecycle hooks.

    Override any method to observe or modify agent loop state.
    All methods are no-ops by default.
    """

    async def before_iteration(self, ctx: IterationContext) -> None:
        """Before each LLM call. ctx.messages is modifiable."""

    async def on_response(self, ctx: IterationContext) -> None:
        """After each LLM response. Read ctx.response/final_content/usage."""

    async def after_tools(self, ctx: IterationContext) -> None:
        """After all tool results are collected. ctx.messages is modifiable."""

    async def after_iteration(self, ctx: IterationContext) -> None:
        """After each loop iteration completes."""

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        """Before a single tool executes. Read ctx.tool_name/tool_args/tool_call_id."""

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        """After a single tool completes. Read ctx.tool_result/tool_error/tool_timeout."""

    async def on_compact(self, ctx: CompactContext) -> None:
        """When context is compacted. Read/write ctx.messages, ctx.old_count/new_count."""


class HookRunner:
    """Fan-out dispatcher for a list of AgentHooks with error isolation."""

    _log = logging.getLogger(__name__)

    def __init__(self, hooks: list[AgentHook] | None = None):
        self._hooks: list[AgentHook] = list(hooks or [])

    def add(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    async def _dispatch(self, method: str, **kwargs) -> None:
        for h in self._hooks:
            try:
                await getattr(h, method)(**kwargs)
            except Exception:
                self._log.debug(
                    "Hook %s.%s failed", type(h).__name__, method, exc_info=True
                )

    async def before_iteration(self, ctx: IterationContext) -> None:
        await self._dispatch("before_iteration", ctx=ctx)

    async def on_response(self, ctx: IterationContext) -> None:
        await self._dispatch("on_response", ctx=ctx)

    async def after_tools(self, ctx: IterationContext) -> None:
        await self._dispatch("after_tools", ctx=ctx)

    async def after_iteration(self, ctx: IterationContext) -> None:
        await self._dispatch("after_iteration", ctx=ctx)

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        await self._dispatch("on_tool_start", ctx=ctx)

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        await self._dispatch("on_tool_complete", ctx=ctx)

    async def on_compact(self, ctx: CompactContext) -> None:
        await self._dispatch("on_compact", ctx=ctx)
