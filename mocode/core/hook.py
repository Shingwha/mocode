"""AgentHook — class-based lifecycle hooks for AgentLoop.

Override methods on AgentHook to observe or intercept the agent loop. Methods
receive typed context objects:

  - IterationContext  (before_iteration / on_response / after_tools / after_iteration)
  - ToolCallContext   (on_tool_start / on_tool_complete)

Hooks may rewrite loop state — ``ctx.messages``, ``ctx.tool_args``,
``ctx.tool_result`` — or veto a tool call by setting ``ctx.deny``. They may
also publish arbitrary events to the host with ``await ctx.emit(event)``; the
loop only transports events and never inspects their types.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .provider import Response, ToolCall, Usage


async def _noop_emit(event: object) -> None:
    """Default event sink for contexts built outside a running loop."""


@dataclass
class IterationContext:
    """before_iteration / on_response / after_tools / after_iteration

    ``messages`` is the live list — a hook may replace its contents in place
    (``ctx.messages[:] = ...``) or rebind it (``ctx.messages = ...``); the loop
    re-reads it after every hook call.
    """

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
    emit: Callable[[object], Awaitable[None]] = _noop_emit


@dataclass
class ToolCallContext:
    """on_tool_start / on_tool_complete — one independent instance per concurrent tool call.

    Interception protocol:
      - ``on_tool_start`` may rewrite ``tool_args``, or set ``deny`` to veto the
        call (the denial text becomes the tool result).
      - ``on_tool_complete`` may rewrite ``tool_result`` before it reaches the model.

    ``status`` is one of ``ok`` / ``error`` / ``timeout`` / ``denied`` / ``not_found``.
    """

    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    deny: str | None = None
    status: str = "ok"
    error_code: str | None = None
    tool_result: str | None = None
    tool_timeout: int | None = None
    emit: Callable[[object], Awaitable[None]] = _noop_emit


@dataclass
class ToolTimingTracker:
    """Reusable tracker for per-call timing and per-tool-name error tracking.

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

    Override any method to observe or intercept agent loop state.
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
        """Before a single tool executes. May rewrite ctx.tool_args or set ctx.deny."""

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        """After a single tool completes. May rewrite ctx.tool_result."""

    async def on_event(self, event: object) -> None:
        """Receives events emitted via ctx.emit(). Ignore what you don't handle."""


class HookRunner:
    """Fan-out dispatcher for a list of AgentHooks with error isolation."""

    _log = logging.getLogger(__name__)

    def __init__(self, hooks: list[AgentHook] | None = None):
        self._hooks: list[AgentHook] = list(hooks or [])

    def add(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    def all(self) -> list[AgentHook]:
        return list(self._hooks)

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

    async def on_event(self, event: object) -> None:
        await self._dispatch("on_event", event=event)
