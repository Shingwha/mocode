"""AgentLoop — LLM chat engine.

One loop drives one conversation, and one turn at a time. A turn is started
with :meth:`AgentLoop.start`, which returns a :class:`Turn`: an addressable
object that can be watched, waited on and cancelled independently of whoever
started it. Every event it produces goes into the loop's
:class:`~mocode.core.channel.EventChannel`, so the run is observable by more
than one reader and outlives any single one of them — a frontend that
reconnects, a status endpoint and a logger can all read the same run.

:meth:`AgentLoop.stream` and :meth:`AgentLoop.chat` are conveniences over
``start()``: they run a turn and scope it to the caller (stop reading, or be
cancelled, and the turn stops). Nothing else gets its own path through the
loop.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, replace
from typing import AsyncIterator, Awaitable, Callable
from uuid import uuid4

from .channel import EventChannel
from .events import (
    Event,
    IterationFinished,
    IterationStarted,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    TextDelta,
    TOOL_DENIED,
    TOOL_ERROR,
    TOOL_NOT_FOUND,
    TOOL_TIMEOUT,
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
from .state import RunState
from .tool import (
    DENIED_PREFIX,
    ERROR_PREFIX,
    TIMEOUT_PREFIX,
    ToolError,
    ToolRegistry,
    split_result,
)
from .turn import Turn


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
    turn, and ``channel`` is where every event goes.

    One instance owns one conversation and runs one turn at a time: a second
    :meth:`start` while one is in flight raises instead of interleaving two
    histories into one.

    What the loop sends is read live from its dependencies on every request —
    the prompt as it stands, the tools as the registry offers them. Freezing
    either half of that request is a *host* decision (see
    :meth:`ToolRegistry.freeze <mocode.core.tool.ToolRegistry.freeze>`): an
    embedder hands the loop whatever view of its tools it wants, and this loop
    simply asks for it.
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
        channel: EventChannel | None = None,
        prepare: "Callable[[], Awaitable[None]] | None" = None,
    ):
        self.provider = provider
        self.model = model if model is not None else ModelSpec(name=provider.model)
        self.system_prompt = system_prompt
        self._tools = tools
        self.hooks = hooks
        self.config = config or AgentConfig()
        #: Awaited at the top of every turn, before the loop captures its
        #: baseline of the prompt: the conversation's chance to make its
        #: request surface (system prompt, offered interface) real. Idempotent
        #: by the caller's convention — the first turn pays, the rest no-op.
        #: Not inherited by ``derive()``: a child runs inside a parent that
        #: has already prepared.
        self.prepare = prepare
        self.messages: list[dict] = []
        #: Where this conversation's events go. Pass one to let a derived agent
        #: report into the same stream as its parent.
        self.channel = channel if channel is not None else EventChannel()
        self._turn: Turn | None = None
        self._idle_state = RunState()
        self._run_id = ""
        self._call_seq = 0
        self._failure: BaseException | None = None
        # Observation is in-band for hooks: the channel awaits them, so a hook
        # still sees every event before the run moves on.
        self._unsubscribe_hooks = self.channel.inline(self.hooks.on_event)

    def derive(
        self,
        *,
        system_prompt: str | None = None,
        tools: ToolRegistry | None = None,
        hooks: HookRunner | None = None,
        config: AgentConfig | None = None,
        model: ModelSpec | None = None,
        provider: Provider | None = None,
        channel: EventChannel | None = None,
    ) -> AgentLoop:
        """Create an independent agent sharing this one's setup.

        History is always fresh, and hooks are *not* inherited unless passed —
        a sub-agent should not inherit the host's display hooks. Tools and
        config are inherited as *copies*: the child keeps the parent's
        capability set but gets its own registry and policy, so a child that
        disables a tool or changes a limit never reaches back into the parent.
        Pass ``provider`` (usually with ``model``) to run the child on a
        different backend or a cheaper model.

        This is the primitive behind sub-agents, workflow nodes and any other
        "run a nested agent with narrower tools" feature. Pass
        ``channel=self.channel`` to have the sub-agent publish into the
        parent's stream instead of a private one: the channel's subscribers
        then see the child's events interleaved on the same timeline — but a
        parent's :class:`Turn` views and each loop's own ``state`` stay scoped
        to their own run, so watch a child through the child's ``Turn``.
        """
        return AgentLoop(
            provider=provider if provider is not None else self.provider,
            system_prompt=self.system_prompt if system_prompt is None else system_prompt,
            tools=self._tools.select() if tools is None else tools,
            hooks=hooks if hooks is not None else HookRunner(),
            config=self.config.replace() if config is None else config,
            model=self.model if model is None else model,
            channel=channel,
        )

    def reset(self) -> None:
        """Drop the conversation and the last turn. Shared deps (provider, prompt, tools) stay."""
        self.messages = []
        self._turn = None
        self._failure = None
        self._idle_state = RunState()

    def close(self) -> None:
        """Detach this loop from its channel's inline subscribers. Idempotent.

        Only the loop's own attachment goes: the channel belongs to whoever
        created it and stays open for other readers. A derived agent that
        borrowed the parent's channel should be closed when it is done, so its
        (empty) hook fan-out does not stay subscribed forever.
        """
        if self._unsubscribe_hooks is not None:
            self._unsubscribe_hooks()
            self._unsubscribe_hooks = None

    # ---- Execution ----

    def start(self, user_input: str | None = None) -> Turn:
        """Begin a turn and return it. Raises if one is already running.

        ``user_input`` is appended to the history first; pass ``None`` to run
        against the history as it stands.
        """
        if self.busy:
            raise RuntimeError(
                "this agent is already running a turn — one conversation runs one "
                "turn at a time; cancel it or wait for it to finish"
            )
        turn = Turn(
            agent=self,
            id=uuid4().hex[:12],
            first_seq=self.channel.seq,
            user_input=user_input,
        )
        # The turn's task cannot start before this returns (creating a task only
        # schedules it), so the loop already owns the turn when it first runs.
        self._turn = turn
        return turn

    def stream(self, user_input: str | None = None) -> AsyncIterator[Event]:
        """Run one turn and yield its events.

        A view of :meth:`start` scoped to this caller: stop iterating — or be
        cancelled — and the turn stops with you. Use ``start()`` when the run
        should outlive the reader. The turn starts when iteration starts, not
        when this is called.
        """
        return self._watched(user_input)

    async def _watched(self, user_input: str | None) -> AsyncIterator[Event]:
        turn = self.start(user_input)
        sub = turn.subscribe()
        try:
            async for event in sub:
                yield event
        finally:
            sub.close()
            turn.cancel()

    async def chat(self, user_input: str | None = None) -> str:
        """One conversation turn, returning the final answer.

        Convenience wrapper over :meth:`start` for callers that only want the
        reply. Provider failures raise, exactly as they would mid-stream, and
        cancelling this coroutine stops the turn.
        """
        turn = self.start(user_input)
        try:
            terminal = await turn.wait()
        except asyncio.CancelledError:
            turn.cancel()
            raise
        if isinstance(terminal, RunFailed):
            raise turn.failure or RuntimeError(terminal.error)
        return terminal.content

    async def run_with_messages(self, messages: list[dict]) -> LoopResult:
        """Run the loop with a pre-existing message list (shallow-copied)."""
        self.messages = list(messages)
        try:
            content = await self.chat(None)
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

    # ---- The turn ----

    async def _drive(self, run_id: str, user_input: str | None) -> RunFinished | RunFailed:
        """Run one turn and return its terminal event — never raises for it.

        Cancellation is a normal ending here, not an exception the caller has to
        catch: the turn belongs to the loop, so whoever stopped it and whoever
        is watching both get the same terminal event. Every path out of here
        goes through :meth:`_publish`, which is what folds the terminal state.
        """
        self._run_id = run_id
        self._failure = None
        turn = self._turn
        try:
            # The surface materializes before the baseline is captured, so a
            # turn never starts from — and never restores — a placeholder.
            if self.prepare is not None:
                await self.prepare()
            prompt_at_start = self.system_prompt
            if turn is not None and turn._begin():
                raise asyncio.CancelledError()
            terminal = await self._iterate(user_input)
        except asyncio.CancelledError:
            self.messages.append({"role": "assistant", "content": self.INTERRUPT_MSG})
            if turn is not None:
                turn.cancelled = True
            terminal = await self._publish(
                RunFinished(
                    content="",
                    cancelled=True,
                    usage=self.state.usage,
                    iterations=self.state.iteration,
                    tool_calls_made=self.state.tool_calls_made,
                )
            )
        except Exception as exc:
            self._failure = exc
            if turn is not None:
                turn.failure = exc
            terminal = await self._publish(
                RunFailed(error=str(exc), kind=type(exc).__name__)
            )
        except BaseException as exc:
            # SystemExit, KeyboardInterrupt, anything else that is not an
            # Exception: the turn still ends exactly like any other failed
            # turn — terminal event published, the exception parked on
            # ``turn.failure`` for whoever wants to re-raise it (``chat()``
            # does). Re-raising here instead would push it straight through
            # the task into the event loop, past every reader.
            self._failure = exc
            if turn is not None:
                turn.failure = exc
            terminal = await self._publish(
                RunFailed(error=str(exc) or type(exc).__name__, kind=type(exc).__name__)
            )
        finally:
            # A hook's before_iteration rewrite of the system prompt is a
            # decision about this run, not about the conversation: the next
            # turn starts from the prompt as it stood before this one.
            self.system_prompt = prompt_at_start
        return terminal

    async def _iterate(self, user_input: str | None) -> RunFinished:
        if user_input is not None:
            self.messages.append({"role": "user", "content": user_input})

        ctx = IterationContext(messages=self.messages, emit=self._emit)
        answer = ""
        iteration = 0

        await self._publish(
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

            await self._publish(IterationStarted(iteration=iteration))

            acc = StreamAccumulator()
            async for chunk in with_retry_stream(
                self.provider,
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
                    await self._publish(ReasoningDelta(text=chunk.reasoning))
                if chunk.text:
                    await self._publish(TextDelta(text=chunk.text))

            response = acc.build()
            await self._publish(
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
                    await self._run_tool_batch(response.tool_calls, results)
                except BaseException:
                    # Cancellation: leave every tool call answered so the
                    # history stays replayable.
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

        return await self._publish(
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
    ) -> None:
        """Run a batch concurrently, in call order, while events flow to the channel.

        The batch is still parallel and its results still land in the order the
        model asked for them; what it no longer needs is a queue to funnel
        events through, because the channel is the ordering point for everyone
        watching.
        """
        tasks = [asyncio.create_task(self._one(call)) for call in tool_calls]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for call, result in zip(tool_calls, results):
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

    async def _one(self, call: ToolCall) -> dict:
        args, parse_error = self._parse_args(call)
        return await self._run_tool(
            call_id=self._next_call_id(call.id),
            provider_id=call.id,
            name=call.name,
            args=args,
            parse_error=parse_error,
        )

    async def _run_tool(
        self,
        *,
        call_id: str,
        provider_id: str,
        name: str,
        args: dict,
        parse_error: str | None,
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
            emit=self._emit,
        )
        if parse_error is None:
            await self.hooks.on_tool_start(tc)

        await self._publish(
            ToolCallStarted(call_id=call_id, name=name, args=dict(tc.tool_args))
        )

        started = time.monotonic()
        if parse_error is not None:
            tc.status = TOOL_ERROR
            tc.tool_result = parse_error
        elif tc.deny:
            tc.status = TOOL_DENIED
            tc.tool_result = f"{DENIED_PREFIX} {tc.deny}"
        else:
            await self._execute_tool(tc)
        duration = time.monotonic() - started

        await self.hooks.on_tool_complete(tc)
        # After the hook, so a rewritten result is bounded like any other.
        tc.tool_result = self._truncate(tc.tool_result or "")

        await self._publish(
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
            tc.status = TOOL_NOT_FOUND
            tc.tool_result = f"{ERROR_PREFIX} unknown tool '{tc.tool_name}'"
            return
        if tc.tool_name not in self._tools.names():
            # Registered but switched off: the frozen interface still offers
            # it, so the model may try — the refusal is the correction.
            tc.status = TOOL_DENIED
            tc.tool_result = f"{DENIED_PREFIX} tool '{tc.tool_name}' is switched off"
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
            # The await is cancelled, not the worker: a sync tool keeps
            # running until it notices the signal. The event tells it to.
            tc.cancel_event.set()
            tc.status = TOOL_TIMEOUT
            tc.tool_timeout = self.config.tool_timeout
            tc.tool_result = f"{TIMEOUT_PREFIX} {self.config.tool_timeout}s"
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

    async def _emit(self, event: Event) -> None:
        """Sink for hooks and tools — publish an event on the run's behalf."""
        await self._publish(event)

    async def _publish(self, event: Event) -> Event:
        """Stamp an event, fold it into the turn's state, and publish it.

        Every event a run produces goes through here, so the channel carries the
        whole run and ``state`` is already up to date by the time an inline
        subscriber (a hook) looks at it.
        """
        event.run_id = self._run_id
        if self._turn is not None:
            self._turn.state.apply(event)
        await self.channel.publish(event)
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

    # ---- State ----

    @property
    def busy(self) -> bool:
        """Whether a turn is running right now."""
        return self._turn is not None and not self._turn.done

    @property
    def turn(self) -> Turn | None:
        """The current or last turn, or ``None`` before the first one."""
        return self._turn

    @property
    def state(self) -> RunState:
        """Live snapshot of the current or last turn."""
        return self._turn.state if self._turn is not None else self._idle_state

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
