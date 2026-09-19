"""AgentHook — the interception channel for AgentLoop.

Hooks and events do different jobs, and the split is deliberate:

* **Events** (:mod:`mocode.core.events`) are one-way notifications. Everything
  the loop does is published there, and a hook that only wants to watch
  implements :meth:`AgentHook.on_event`.
* **Hooks** are request/response. They run at fixed points and may rewrite loop
  state — ``ctx.messages``, ``ctx.tool_args``, ``ctx.tool_result`` — or veto a
  tool call by setting ``ctx.deny``.

Keeping them apart is what makes the event stream a usable contract: a
notification never has to wait for an answer. A hook that *does* answer is
observable anyway, because its decision shows up in the events it caused — a
denied call appears as ``ToolCallFinished(status="denied")``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from .events import TOOL_OK, ToolStatus

if TYPE_CHECKING:
    from .events import Event

#: Signature of ``ctx.emit`` — the sink a context uses to publish an event.
EmitFn = Callable[["Event"], Awaitable[None]]


async def _noop_emit(event: "Event") -> None:
    """Default event sink for contexts built outside a running loop."""


@dataclass
class IterationContext:
    """before_iteration — where a hook may rewrite what the next call sends.

    Both fields are read back by the loop after the hook returns, so a change
    sticks: ``system_prompt`` for the rest of the run (restored when the turn
    ends — a conversation does not inherit one turn's prompt), ``messages`` for
    good.

    ``messages`` is the live list — a hook may replace its contents in place
    (``ctx.messages[:] = ...``) or rebind it (``ctx.messages = ...``).
    """

    messages: list[dict] = field(default_factory=list)
    iteration: int = 0
    system_prompt: str = ""
    emit: EmitFn = _noop_emit


@dataclass
class ToolCallContext:
    """on_tool_start / on_tool_complete — one independent instance per concurrent tool call.

    The same object is handed to the tool itself when it declares a second
    parameter, so a long-running tool can publish progress through ``emit``
    and read the arguments a hook may have rewritten.

    Interception protocol:
      - ``on_tool_start`` may rewrite ``tool_args``, or set ``deny`` to veto the
        call (the denial text becomes the tool result).
      - ``on_tool_complete`` may rewrite ``tool_result`` before it reaches the
        model, or enrich ``tool_details`` for whoever is watching.

    ``status`` is one of the ``TOOL_*`` constants from
    :mod:`mocode.core.events` (:data:`ToolStatus`) — ``ok`` while it runs its
    course, then whichever outcome the loop recorded.

    ``tool_result`` is what the model reads; ``tool_details`` is structured data
    for everything else and never enters the conversation.
    """

    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    deny: str | None = None
    status: ToolStatus = TOOL_OK
    error_code: str | None = None
    tool_result: str | None = None
    tool_details: dict = field(default_factory=dict)
    tool_timeout: int | None = None
    emit: EmitFn = _noop_emit
    #: Set when the loop stops waiting for this call — a timeout, or the turn
    #: being cancelled. Async tools are unwound by cancellation; a sync tool
    #: keeps its worker thread and must notice this signal itself.
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def cancelled(self) -> bool:
        """Whether the caller stopped waiting: check this in long loops.

        ``tool_timeout`` is cooperative for sync tools — the loop gives up
        waiting, sets this flag, and answers the model; the tool's side
        effects continue until the tool notices and returns.
        """
        return self.cancel_event.is_set()


class AgentHook:
    """Base class for AgentLoop lifecycle hooks.

    Override any method to intercept. All methods are no-ops by default, and a
    raising hook never breaks the loop — :class:`HookRunner` isolates failures.
    """

    async def before_iteration(self, ctx: IterationContext) -> None:
        """Before each LLM call. May rewrite ctx.messages or ctx.system_prompt."""

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        """Before a single tool executes. May rewrite ctx.tool_args or set ctx.deny."""

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        """After a single tool completes. May rewrite ctx.tool_result."""

    async def on_event(self, event: "Event") -> None:
        """Every event the run publishes, including plugin ones. Ignore what you don't handle."""


class HookRunner:
    """Fan-out dispatcher for a list of AgentHooks with error isolation.

    Hooks run in the order they were added — at the host that is plugin load
    order (built-ins first, then each plugin directory, alphabetical inside
    one), and within one plugin, the order ``build()`` added them. That order
    is deterministic but it is not a dependency graph: a hook that must not be
    undone by a later one should not rely on position alone.

    A raising hook never breaks the loop — each call is isolated and logged at
    WARNING, so a faulty hook is visible without taking the run down.
    """

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
                self._log.warning(
                    "Hook %s.%s failed", type(h).__name__, method, exc_info=True
                )

    async def before_iteration(self, ctx: IterationContext) -> None:
        await self._dispatch("before_iteration", ctx=ctx)

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        await self._dispatch("on_tool_start", ctx=ctx)

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        await self._dispatch("on_tool_complete", ctx=ctx)

    async def on_event(self, event: "Event") -> None:
        await self._dispatch("on_event", event=event)
