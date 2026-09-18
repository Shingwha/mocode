"""AgentLoop — LLM chat engine.

:meth:`AgentLoop.stream` is the only execution entry point. It publishes one
ordered :class:`~mocode.core.events.Event` per thing that happens — model text
as it arrives, tool calls as they start and finish — and ``chat()`` is a thin
wrapper that runs it to completion and returns the final answer.

Cancellation propagates as ``asyncio.CancelledError``; whatever work is in
flight is torn down in ``finally`` blocks and the conversation is left in a
consistent state.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from .events import (
    Event,
    IterationFinished,
    IterationStarted,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
)
from .hook import HookRunner, IterationContext, ToolCallContext
from .provider import (
    ModelSpec,
    Provider,
    StreamAccumulator,
    ToolCall,
    Usage,
    with_retry_stream,
)
from .state import CANCELLED, FAILED, RUNNING, RunState
from .tool import (
    DENIED_PREFIX,
    ERROR_PREFIX,
    TIMEOUT_PREFIX,
    ToolError,
    ToolRegistry,
    split_result,
)

#: Marks one finished task inside a tool batch. A sentinel rather than a
#: callback so the queue stays the single ordering point for batch events.
_BATCH_DONE = object()


@dataclass
class AgentConfig:
    """Loop execution policy. Facts about the model live in :class:`ModelSpec`."""

    tool_result_limit: int = 50000
    tool_timeout: int = 240
    max_iterations: int = 0  # 0 = unlimited

    def replace(self, **changes) -> AgentConfig:
        """Return a copy with the given fields changed (e.g. for derived agents)."""
        return replace(self, **changes)


@dataclass
class LoopResult:
    content: str = ""
    tool_calls_made: int = 0
    messages: list[dict] = field(default_factory=list)
    had_error: bool = False


class AgentLoop:
    """LLM chat engine — receives all dependencies via constructor.

    Public mutable state: ``provider`` (swappable at runtime), ``system_prompt``,
    ``messages``, ``hooks``. ``state`` is a live snapshot of the current or last
    turn. Derived agents are created with :meth:`derive`.

    One instance runs one turn at a time — ``messages`` and ``state`` are shared.
    """

    INTERRUPT_MSG = "[Response was interrupted by the user before completion.]"
    INTERRUPT_TOOL_MSG = (
        "[Tool execution was interrupted by the user before completion.]"
    )

    def __init__(
        self,
        provider: Provider,
        system_prompt: str,
        tools: ToolRegistry,
        hooks: HookRunner,
        config: AgentConfig | None = None,
        model: ModelSpec | None = None,
    ):
        self.provider = provider
        self.model = model if model is not None else ModelSpec(name=provider.model)
        self.system_prompt = system_prompt
        self._tools = tools
        self.hooks = hooks
        self.config = config or AgentConfig()
        self.messages: list[dict] = []
        self.state = RunState()
        self._run_id = ""
        self._seq = 0
        self._call_seq = 0
        self._failure: BaseException | None = None

    def derive(
        self,
        *,
        system_prompt: str | None = None,
        tools: ToolRegistry | None = None,
        hooks: HookRunner | None = None,
        config: AgentConfig | None = None,
        model: ModelSpec | None = None,
    ) -> AgentLoop:
        """Create an independent agent that shares this one's provider.

        Message history is always fresh, and hooks are *not* inherited unless
        passed — a sub-agent should not inherit the host's display hooks. Every
        other field is inherited unless overridden. This is the primitive behind
        sub-agents, workflow nodes and any other "run a nested agent with
        narrower tools" feature.
        """
        return AgentLoop(
            provider=self.provider,
            system_prompt=self.system_prompt if system_prompt is None else system_prompt,
            tools=self._tools if tools is None else tools,
            hooks=hooks if hooks is not None else HookRunner(),
            config=self.config if config is None else config,
            model=self.model if model is None else model,
        )

    def reset(self) -> None:
        """Reset mutable state for reuse. Shared deps (provider, prompt, tools) stay."""
        self.messages = []
        self.state = RunState()
        self._seq = 0
        self._call_seq = 0
        self._failure = None

    # ---- Execution ----

    async def stream(
        self,
        user_input: str | None = None,
        *,
        images: list[str] | None = None,
    ) -> AsyncIterator[Event]:
        """Run one turn, yielding every event as it happens.

        ``user_input`` is appended to the history first; pass ``None`` to run
        against the history as it stands. Cancel by cancelling the task that is
        consuming this — the loop is torn down on the way out.

        The stream ends with exactly one of :class:`RunFinished` (the turn
        completed, including a cancelled one) or :class:`RunFailed` (an
        unhandled error). A turn that was cancelled outright publishes neither:
        there is nobody left to read it.
        """
        if user_input is not None:
            content = (
                self._build_user_content(user_input, images) if images else user_input
            )
            self.messages.append({"role": "user", "content": content})

        self._run_id = uuid4().hex[:12]
        self._seq = 0
        self._failure = None
        self.state = RunState()

        try:
            async for event in self._loop():
                yield event
        except asyncio.CancelledError:
            self.messages.append({"role": "assistant", "content": self.INTERRUPT_MSG})
            raise
        except Exception as exc:
            self._failure = exc
            yield await self._publish(RunFailed(error=str(exc), kind=type(exc).__name__))
        finally:
            if self.state.status == RUNNING:
                self.state.status = FAILED if self._failure else CANCELLED

    async def chat(self, user_input: str, images: list[str] | None = None) -> str:
        """One conversation turn, returning the final answer.

        Convenience wrapper over :meth:`stream` for callers that only want the
        reply. Provider failures raise, exactly as they would mid-stream.
        """
        return await self._drain(user_input, images)

    async def run_with_messages(self, messages: list[dict]) -> LoopResult:
        """Run the loop with a pre-existing message list (shallow-copied)."""
        self.messages = list(messages)
        try:
            content = await self._drain(None, None)
            return LoopResult(
                content=content,
                tool_calls_made=self.tool_call_count,
                messages=self.messages,
            )
        except Exception as e:
            return LoopResult(
                content=str(e),
                tool_calls_made=self.tool_call_count,
                messages=self.messages,
                had_error=True,
            )

    async def _drain(self, user_input: str | None, images: list[str] | None) -> str:
        async for event in self.stream(user_input, images=images):
            if isinstance(event, RunFinished):
                return event.content
        if self._failure is not None:
            raise self._failure
        return ""

    # ---- The loop ----

    async def _loop(self) -> AsyncIterator[Event]:
        # Events a hook publishes through ctx.emit arrive between yields, so
        # they are buffered here and handed to the consumer as soon as the hook
        # returns. Tools get a different sink (see _run_tool_batch) because
        # theirs arrive concurrently.
        pending: list[Event] = []

        async def emit(event: Event) -> None:
            await self._publish(event)
            pending.append(event)

        ctx = IterationContext(messages=self.messages, emit=emit)
        answer = ""
        iteration = 0

        yield await self._publish(
            RunStarted(model=self.model.name, tools=self._tools.names())
        )

        while True:
            iteration += 1
            ctx.messages = self.messages
            ctx.iteration = iteration
            ctx.system_prompt = self.system_prompt

            await self.hooks.before_iteration(ctx)
            self.messages = ctx.messages
            self.system_prompt = ctx.system_prompt
            for event in pending:
                yield event
            pending.clear()

            yield await self._publish(IterationStarted(iteration=iteration))

            acc = StreamAccumulator()
            async for chunk in with_retry_stream(
                self.provider,
                self.provider.stream,
                self.messages,
                self.system_prompt,
                self._tools.all_schemas(),
                self.model.max_output,
            ):
                acc.feed(chunk)
                # Reasoning before text. An endpoint switching a model from
                # thinking to answering sometimes puts the last reasoning
                # fragment and the first answer fragment in the *same* delta;
                # emitting them the other way round makes a renderer bounce
                # between the two blocks mid-sentence.
                if chunk.reasoning:
                    yield await self._publish(ReasoningDelta(text=chunk.reasoning))
                if chunk.text:
                    yield await self._publish(TextDelta(text=chunk.text))

            response = acc.build()
            yield await self._publish(
                IterationFinished(
                    iteration=iteration,
                    usage=response.usage,
                    stop_reason=response.finish_reason,
                )
            )

            if response.tool_calls:
                assistant = self._assistant_msg(
                    response, self._tool_call_dicts(response.tool_calls)
                )
                results: list[dict] = []
                try:
                    async for event in self._run_tool_batch(response.tool_calls, results):
                        yield event
                except BaseException:
                    # Cancellation, or the consumer closing us mid-batch: leave
                    # every tool call answered so the history stays replayable.
                    self.messages.append(assistant)
                    self.messages.extend(self._interrupt_results(response.tool_calls))
                    raise

                self.messages.append(assistant)
                self.messages.extend(results)

                if (
                    self.config.max_iterations > 0
                    and iteration >= self.config.max_iterations
                ):
                    break
            else:
                self.messages.append(self._assistant_msg(response))
                answer = response.content or ""
                break

        yield await self._publish(
            RunFinished(
                content=answer,
                usage=self.state.usage,
                iterations=iteration,
                tool_calls_made=self.state.tool_calls_made,
            )
        )

    # ---- Tool execution ----

    async def _run_tool_batch(
        self, tool_calls: list[ToolCall], out: list[dict]
    ) -> AsyncIterator[Event]:
        """Run a batch concurrently, yielding each call's events as they land.

        Tools run as sibling tasks so the batch stays parallel, but every event
        from all of them funnels through one queue drained here. That is what
        makes a slow tool stop holding back the others' output, while keeping
        this generator the only place that yields.
        """
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: Event) -> None:
            """Sink for tools and tool hooks — published and handed over immediately."""
            await self._publish(event)
            queue.put_nowait(event)

        async def _one(call: ToolCall) -> dict:
            try:
                args, parse_error = self._parse_args(call)
                return await self._run_tool(
                    call_id=self._next_call_id(call.id),
                    provider_id=call.id,
                    name=call.name,
                    args=args,
                    parse_error=parse_error,
                    emit=emit,
                )
            finally:
                queue.put_nowait(_BATCH_DONE)

        tasks = [asyncio.create_task(_one(call)) for call in tool_calls]

        try:
            pending = len(tasks)
            while pending:
                event = await queue.get()
                if event is _BATCH_DONE:
                    pending -= 1
                else:
                    yield event

            for call, result in zip(tool_calls, await asyncio.gather(*tasks, return_exceptions=True)):
                if isinstance(result, BaseException):
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": f"{ERROR_PREFIX} {result}",
                        }
                    )
                else:
                    out.append(result)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_tool(
        self,
        *,
        call_id: str,
        provider_id: str,
        name: str,
        args: dict,
        parse_error: str | None,
        emit,
    ) -> dict:
        """Run one tool call, publishing Started/Output/Finished around it.

        Hooks intercept at two points: ``on_tool_start`` (rewrite args, veto via
        deny) and ``on_tool_complete`` (rewrite the result). ``status`` records
        the structured outcome so callers never parse the result text. The first
        event is published *after* ``on_tool_start``, so a consumer only ever
        sees final arguments.
        """
        tc = ToolCallContext(
            tool_name=name,
            tool_args=args,
            tool_call_id=call_id,
            emit=emit,
        )
        if parse_error is None:
            await self.hooks.on_tool_start(tc)

        await emit(
            ToolCallStarted(call_id=call_id, name=name, args=dict(tc.tool_args))
        )

        started = time.monotonic()
        if parse_error is not None:
            tc.status = "error"
            tc.tool_result = parse_error
        elif tc.deny:
            tc.status = "denied"
            tc.tool_result = f"{DENIED_PREFIX} {tc.deny}"
        else:
            await self._execute_tool(tc)
        duration = time.monotonic() - started

        tc.tool_result = self._truncate(tc.tool_result or "")
        await self.hooks.on_tool_complete(tc)

        await emit(
            ToolCallFinished(
                call_id=call_id,
                name=name,
                status=tc.status,
                result=tc.tool_result,
                error_code=tc.error_code,
                duration=duration,
                details=tc.tool_details,
            )
        )
        return {"role": "tool", "tool_call_id": provider_id, "content": tc.tool_result}

    async def _execute_tool(self, tc: ToolCallContext) -> None:
        """Execute tc's tool, recording status/result on the context."""
        tool = self._tools.get(tc.tool_name)
        if tool is None:
            tc.status = "not_found"
            tc.tool_result = f"unknown tool '{tc.tool_name}'"
            return

        try:
            if tool.is_async:
                result = await asyncio.wait_for(
                    tool.run_async(tc.tool_args, tc),
                    timeout=self.config.tool_timeout,
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(tool.run, tc.tool_args, tc),
                    timeout=self.config.tool_timeout,
                )
        except asyncio.TimeoutError:
            tc.status = "timeout"
            tc.tool_timeout = self.config.tool_timeout
            tc.tool_result = f"{TIMEOUT_PREFIX} {self.config.tool_timeout}s"
        except ToolError as e:
            tc.status = "error"
            tc.error_code = e.code
            tc.tool_result = f"{e.code}: {e.message}"
        except Exception as e:
            tc.status = "error"
            tc.tool_result = f"{ERROR_PREFIX} {e}"
        else:
            tc.tool_result, tc.tool_details = split_result(result)

    @staticmethod
    def _parse_args(call: ToolCall) -> tuple[dict, str | None]:
        """Parse the model's argument JSON, or explain why it could not be."""
        try:
            args = json.loads(call.arguments)
        except json.JSONDecodeError as exc:
            return {}, f"{ERROR_PREFIX} invalid JSON arguments ({exc})"
        if not isinstance(args, dict):
            return {}, f"{ERROR_PREFIX} tool arguments must be a JSON object"
        return args, None

    def _truncate(self, result: str) -> str:
        limit = self.config.tool_result_limit
        if limit > 0 and len(result) > limit:
            return result[:limit] + "\n... [truncated]"
        return result

    # ---- Helpers ----

    async def _publish(self, event: Event) -> Event:
        """Stamp an event, hand it to the hooks, and return it for the consumer.

        Every event a run produces goes through here, so ``on_event`` sees the
        whole run — the loop's own events included, not just plugin ones — and
        ``self.state`` is already up to date by the time a hook looks at it.
        """
        self._seq += 1
        event.run_id = self._run_id
        event.seq = self._seq
        self.state.apply(event)
        await self.hooks.on_event(event)
        return event

    def _next_call_id(self, provider_id: str = "") -> str:
        """Identity for a tool call, shared by its events and its context.

        Prefer the provider's own id so events correlate directly with the tool
        message in the history; synthesize one when an endpoint omits it.
        """
        self._call_seq += 1
        return provider_id or f"call_{self._call_seq}"

    @staticmethod
    def _tool_call_dicts(tool_calls: list[ToolCall]) -> list[dict]:
        return [
            {
                "id": t.id,
                "type": "function",
                "function": {"name": t.name, "arguments": t.arguments},
            }
            for t in tool_calls
        ]

    def _interrupt_results(self, tool_calls: list[ToolCall]) -> list[dict]:
        return [
            {
                "role": "tool",
                "tool_call_id": t.id,
                "content": self.INTERRUPT_TOOL_MSG,
            }
            for t in tool_calls
        ]

    @staticmethod
    def _assistant_msg(response, tool_calls=None) -> dict:
        msg: dict = {"role": "assistant", "content": response.content or ""}
        if response.reasoning_content:
            msg["reasoning_content"] = response.reasoning_content
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    _IMG_MEDIA = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }

    @staticmethod
    def _build_user_content(text: str, images: list[str]) -> list[dict] | str:
        parts: list[dict] = []

        for path_str in images:
            p = Path(path_str)
            if not p.exists() or p.suffix.lower() not in AgentLoop._IMG_MEDIA:
                continue
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                media_type = AgentLoop._IMG_MEDIA[p.suffix.lower()]
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{b64}"},
                    }
                )
            except Exception:
                continue

        if not parts:
            return text
        if text:
            parts.append({"type": "text", "text": text})
        return parts

    @property
    def tool_registry(self) -> ToolRegistry:
        """Public access to the agent's tool registry."""
        return self._tools

    @property
    def iteration(self) -> int:
        """LLM calls made in the current or last turn."""
        return self.state.iteration

    @property
    def tool_call_count(self) -> int:
        """Tool calls started in the current or last turn."""
        return self.state.tool_calls_made

    @property
    def last_usage(self) -> Usage | None:
        """Usage of the last completed iteration."""
        return self.state.last_usage

    @property
    def total_usage(self) -> Usage:
        """Usage summed over the current or last turn."""
        return self.state.usage
