"""ToolDispatcher — the one execution path for a tool call.

Executing a tool is policy, not orchestration: hooks intercept, visibility is
checked, timeout and cooperative cancellation are enforced, outcomes are
mapped to statuses and prefixes, results are truncated, and Started/Finished
events carry the outcome to every reader. That pipeline used to live in
``AgentLoop``'s private methods, which meant anything wanting to run a tool
*outside* a model turn — a plugin orchestrating nested calls, a sub-agent
tool, a workflow node — had to copy it, and a copy drifts: a forgotten
switched-off check bypasses the freeze semantics, a forgotten truncation
pours a 50KB result into a program context, a forgotten ``cancel_event``
disables cooperative cancellation. The dispatcher is that pipeline as a
public core component; the loop itself is now a thin wrapper that turns a
:class:`DispatchResult` into a tool message.

The **program-origin contract**. A call with ``origin="program"`` is one made
on the program's own behalf — nested inside a parent call or between turns:

* its events go to the channel like any other, so every reader can observe
  and audit them, stamped with the run they belong to;
* it never enters ``messages`` — the conversation is what the model said and
  was answered, and a program's tool use is not a reply the model owes;
* it does not fold into the parent turn's ``tool_calls_made`` count — the
  live state stays the model's side of the story.

The dispatcher's events reach the channel through an injected ``publish``
callback; the dispatcher decides, per call, which of the two paths an event
takes (folded for model origin, stamp-only for program origin), so the two
transports can never drift apart again.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from .events import (
    TOOL_DENIED,
    TOOL_ERROR,
    TOOL_NOT_FOUND,
    TOOL_TIMEOUT,
    ToolCallFinished,
    ToolCallStarted,
    ToolStatus,
)
from .hook import HookRunner, ToolCallContext
from .tool import (
    DENIED_PREFIX,
    ERROR_PREFIX,
    TIMEOUT_PREFIX,
    ToolError,
    ToolRegistry,
    split_result,
)

if TYPE_CHECKING:
    from .agent import AgentConfig
    from .events import Event

#: The publisher a dispatcher hands its events to, called as
#: ``publish(event, fold=...)``. ``fold=True`` is the model-origin path — the
#: event is stamped and folded into the run's live state; ``fold=False`` is
#: the program-origin path — stamped and sent, never folded. Whoever owns the
#: channel provides it (``AgentLoop`` does; a bare-core test may record).
PublishFn = Callable[..., Awaitable[None]]

#: Who asked for a tool to run: the model in a turn, or the program itself.
Origin = Literal["model", "program"]


@dataclass
class DispatchResult:
    """What came back from one tool call.

    ``content`` is the model-readable text — prefixed on failure, truncated to
    the configured limit. ``details`` is the structured half and never enters
    ``messages``: a frontend or an application reads it, the model does not.
    """

    status: ToolStatus
    content: str
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    duration: float = 0.0
    call_id: str = ""


class ToolDispatcher:
    """The full policy pipeline around one tool call.

    Hooks intercept at two points — ``on_tool_start`` before the call is even
    announced (a consumer only ever sees final arguments), ``on_tool_complete``
    before the result is bounded and published. Between them: the
    switched-off check, ``asyncio.wait_for`` timeout with cooperative
    cancellation for sync tools, ``ToolError`` → status/error_code mapping,
    and the result split into model-readable text and structured details.

    ``AgentLoop`` runs every model-origin call through here, and anything
    driving tools of its own calls :meth:`run` with ``origin="program"`` —
    same hooks, same visibility rules, same truncation, same events, so the
    behaviour cannot drift between the two.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        hooks: HookRunner,
        config: "AgentConfig",
        publish: PublishFn,
    ):
        self.registry = registry
        self.hooks = hooks
        self.config = config
        self._publish = publish
        self._call_seq = 0
        self._nested_seq: dict[str, int] = {}

    async def run(
        self,
        name: str,
        args: dict,
        *,
        call_id: str = "",
        parse_error: str | None = None,
        origin: Origin = "model",
        parent_call_id: str | None = None,
        timeout: int | None = None,
    ) -> DispatchResult:
        """Run one tool call through the pipeline and report what came back.

        ``origin="model"`` is a call the model asked for: pass the provider's
        own ``call_id`` (one is synthesized when the endpoint omitted it), the
        parsed ``args``, and ``parse_error`` set when the arguments would not
        parse. ``origin="program"`` is a call made on the program's behalf:
        the dispatcher assigns its identity — ``<parent_call_id>:<n>`` nested
        inside a parent call, ``pcall_<n>`` standalone — and its events take
        the stamp-only path (see the module docstring for the contract).

        ``timeout`` overrides ``AgentConfig.tool_timeout`` for this one call.
        """
        fold = origin == "model"
        cid = self._assign_call_id(call_id, origin, parent_call_id)

        async def emit(event: "Event") -> None:
            await self._publish(event, fold=fold)

        tc = ToolCallContext(
            tool_name=name,
            tool_args=args,
            tool_call_id=cid,
            origin=origin,
            parent_call_id=parent_call_id,
            emit=emit,
        )
        if parse_error is None:
            await self.hooks.on_tool_start(tc)

        await self._publish(
            ToolCallStarted(
                call_id=cid,
                name=name,
                args=dict(tc.tool_args),
                origin=origin,
                parent_call_id=parent_call_id,
            ),
            fold=fold,
        )

        started = time.monotonic()
        if parse_error is not None:
            tc.status = TOOL_ERROR
            tc.tool_result = parse_error
        elif tc.deny:
            tc.status = TOOL_DENIED
            tc.tool_result = f"{DENIED_PREFIX} {tc.deny}"
        else:
            await self._execute(tc, timeout)
        duration = time.monotonic() - started

        await self.hooks.on_tool_complete(tc)
        # After the hook, so a rewritten result is bounded like any other.
        tc.tool_result = self._truncate(tc.tool_result or "")

        await self._publish(
            ToolCallFinished(
                call_id=cid,
                name=name,
                status=tc.status,
                result=tc.tool_result,
                error_code=tc.error_code,
                duration=duration,
                details=tc.tool_details,
                origin=origin,
                parent_call_id=parent_call_id,
            ),
            fold=fold,
        )
        return DispatchResult(
            status=tc.status,
            content=tc.tool_result,
            details=tc.tool_details,
            error_code=tc.error_code,
            duration=duration,
            call_id=cid,
        )

    # ── Execution ──────────────────────────────────────────

    async def _execute(self, tc: ToolCallContext, timeout: int | None) -> None:
        """Execute the context's tool, recording status/result on it."""
        tool = self.registry.get(tc.tool_name)
        if tool is None:
            tc.status = TOOL_NOT_FOUND
            tc.tool_result = f"{ERROR_PREFIX} unknown tool '{tc.tool_name}'"
            return
        if tc.tool_name not in self.registry.names():
            # Registered but switched off: the frozen interface still offers
            # it, so the model may try — the refusal is the correction.
            tc.status = TOOL_DENIED
            tc.tool_result = f"{DENIED_PREFIX} tool '{tc.tool_name}' is switched off"
            return

        limit = self.config.tool_timeout if timeout is None else timeout
        try:
            if tool.is_async:
                result = await asyncio.wait_for(
                    tool.run_async(tc.tool_args, tc), timeout=limit
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(tool.run, tc.tool_args, tc), timeout=limit
                )
        except asyncio.TimeoutError:
            # The await is cancelled, not the worker: a sync tool keeps
            # running until it notices the signal. The event tells it to.
            tc.cancel_event.set()
            tc.status = TOOL_TIMEOUT
            tc.tool_timeout = limit
            tc.tool_result = f"{TIMEOUT_PREFIX} {limit}s"
        except asyncio.CancelledError:
            # Same cooperative signal for a turn cancelled mid-call.
            tc.cancel_event.set()
            raise
        except ToolError as e:
            tc.status = TOOL_ERROR
            tc.error_code = e.code
            tc.tool_result = f"{ERROR_PREFIX} {e.code}: {e.message}"
        except Exception as e:
            tc.status = TOOL_ERROR
            tc.tool_result = f"{ERROR_PREFIX} {e}"
        else:
            tc.tool_result, tc.tool_details = split_result(result)

    def _truncate(self, result: str) -> str:
        limit = self.config.tool_result_limit
        if limit > 0 and len(result) > limit:
            return result[:limit] + "\n... [truncated]"
        return result

    def _assign_call_id(
        self, call_id: str, origin: Origin, parent_call_id: str | None
    ) -> str:
        """Identity for a tool call, shared by its events and its context.

        A model-origin call prefers the provider's own id, so events correlate
        directly with the tool message in the history; one is synthesized when
        an endpoint omits it. A program-origin call gets a structured identity
        instead: ``<parent_call_id>:<n>`` when nested inside a parent call —
        numbered per parent, so siblings read as a series a UI can fold — and
        ``pcall_<n>`` when standalone. The two counters share one sequence, so
        synthesized ids never collide across origins.
        """
        self._call_seq += 1
        if origin == "program":
            if parent_call_id:
                n = self._nested_seq.get(parent_call_id, 0) + 1
                self._nested_seq[parent_call_id] = n
                return f"{parent_call_id}:{n}"
            return f"pcall_{self._call_seq}"
        return call_id or f"call_{self._call_seq}"


__all__ = ["DispatchResult", "Origin", "PublishFn", "ToolDispatcher"]
