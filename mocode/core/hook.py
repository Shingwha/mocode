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
from typing import TYPE_CHECKING, Awaitable, Callable, Literal

from .events import TOOL_OK, ToolStatus

if TYPE_CHECKING:
    from .events import Event
    from .provider import ModelSpec, Usage

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
    #: The conversation's event sink — ``await ctx.emit(event)`` publishes on
    #: this run's channel, attributed to the run. A hook that watches or
    #: explains has something to say here without needing a channel of its own.
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

    Provenance: ``origin`` says who asked for this call — ``"model"`` when the
    model asked for it in a turn, ``"program"`` when code (a plugin, a
    sub-agent tool) ran it on its own behalf — and ``parent_call_id`` names the
    call a program-origin call is nested inside. A hook no longer has to parse
    ``tool_call_id`` strings to tell the two apart. The program-origin
    contract, enforced by :class:`~mocode.core.dispatch.ToolDispatcher`: those
    calls' events reach the channel stamped with the run they belong to, but
    never enter ``messages`` and never fold into the parent turn's
    ``tool_calls_made`` count.
    """

    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    origin: Literal["model", "program"] = "model"
    parent_call_id: str | None = None
    deny: str | None = None
    status: ToolStatus = TOOL_OK
    error_code: str | None = None
    tool_result: str | None = None
    tool_details: dict = field(default_factory=dict)
    #: The execution policy this call actually runs under, resolved by the
    #: dispatcher (call-level over tool-level over config) before the tool
    #: runs. ``tool_timeout`` bounds the whole call in seconds; a sync tool
    #: that outlives it notices via ``cancel_event``.
    tool_timeout: int | None = None
    #: The character budget applied to the result before it reaches the model.
    tool_result_limit: int | None = None
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


@dataclass
class RequestContext:
    """before_request — the last look at what is about to be sent.

    Runs after ``before_iteration``, immediately before the provider call.
    ``messages`` is the live list — rewrite in place (``ctx.messages[:] =
    ...``) or rebind (``ctx.messages = [...]``); both are read back.
    ``system_prompt`` follows the same run-scoped rule as before_iteration's:
    effective for the rest of this run, restored when the turn ends.
    ``tools`` is the schema list this one request carries — an in-place edit
    reaches this request alone, never the registry or a frozen payload.

    Retries do *not* re-run this hook: the retry window closes before the
    first chunk arrives and belongs to the retry orchestration, not to
    request interception.
    """

    messages: list[dict] = field(default_factory=list)
    system_prompt: str = ""
    tools: list[dict] = field(default_factory=list)
    model: "ModelSpec | None" = None
    #: The conversation's event sink — ``await ctx.emit(event)`` publishes on
    #: this run's channel, attributed to the run.
    emit: EmitFn = _noop_emit


@dataclass
class ResponseContext:
    """after_response — one provider response, fully received.

    ``usage`` may be rewritten: the corrected numbers are what
    ``IterationFinished`` reports and what the turn's totals add up to.
    ``finish_reason`` and ``iteration`` are informational.
    """

    usage: "Usage | None" = None
    finish_reason: str | None = None
    iteration: int = 0


class AgentHook:
    """Base class for AgentLoop lifecycle hooks.

    Override any method to intercept. All methods are no-ops by default, and a
    raising hook never breaks the loop — :class:`HookRunner` isolates failures.
    """

    async def before_iteration(self, ctx: IterationContext) -> None:
        """Before each LLM call. May rewrite ctx.messages or ctx.system_prompt."""

    async def before_request(self, ctx: RequestContext) -> None:
        """The last look before a provider call. May rewrite ctx.messages or
        ctx.system_prompt, or edit ctx.tools in place.

        Runs once per *request actually made* — after ``before_iteration``.
        Provider retries do not re-run it: the retry window closes before the
        first chunk arrives and is owned by the retry orchestration.
        """

    async def after_response(self, ctx: ResponseContext) -> None:
        """One provider response fully received. May rewrite ctx.usage — the
        correction flows into the events and the turn's token totals.

        Like before_request, not re-run for retried attempts: a response only
        exists once its stream has delivered a first chunk.
        """

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
        """Append *hook* to the end of the fan-out order.

        ``build()`` adding hooks one by one is how plugin load order becomes
        dispatch order — built-ins first, then each plugin directory
        alphabetically, and inside one plugin the order ``build()`` added
        them.
        """
        self._hooks.append(hook)

    async def _dispatch(self, method: str, **kwargs) -> None:
        for h in self._hooks:
            try:
                await getattr(h, method)(**kwargs)
            except Exception:
                self._log.warning(
                    "Hook %s.%s failed", type(h).__name__, method, exc_info=True
                )

    async def before_iteration(self, ctx: IterationContext) -> None:
        """Every hook's ``before_iteration``, in registration order."""
        await self._dispatch("before_iteration", ctx=ctx)

    async def before_request(self, ctx: RequestContext) -> None:
        """Every hook's ``before_request``, in registration order."""
        await self._dispatch("before_request", ctx=ctx)

    async def after_response(self, ctx: ResponseContext) -> None:
        """Every hook's ``after_response``, in registration order."""
        await self._dispatch("after_response", ctx=ctx)

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        """Every hook's ``on_tool_start``, in registration order."""
        await self._dispatch("on_tool_start", ctx=ctx)

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        """Every hook's ``on_tool_complete``, in registration order."""
        await self._dispatch("on_tool_complete", ctx=ctx)

    async def on_event(self, event: "Event") -> None:
        """Every hook's ``on_event``, in registration order.

        The in-band subscriber: the publisher waits for this to return.
        """
        await self._dispatch("on_event", event=event)
