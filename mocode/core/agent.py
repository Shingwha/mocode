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
from .dispatch import ToolDispatcher
from .events import (
    Event,
    IterationFinished,
    IterationStarted,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    StopReason,
    TextDelta,
    ToolCallArgsDelta,
)
from .hook import (
    HookRunner,
    IterationContext,
    RequestContext,
    ResponseContext,
)
from .provider import (
    ModelSpec,
    Provider,
    RetryDeadlineExceeded,
    StreamAccumulator,
    ToolCall,
    with_retry_stream,
)
from .state import RunState
from .tool import ERROR_PREFIX, ToolRegistry
from .transcript import assistant_message, tool_call_dicts, tool_result
from .turn import Turn


@dataclass
class AgentConfig:
    """Loop execution policy. Facts about the model live in :class:`ModelSpec`.

    The budget fields are per turn and 0 means unlimited. All three are
    checked before each provider call, and the wall clock additionally
    bounds the retry backoff itself: a turn a budget cuts ends with the
    matching ``RunFinished.stop_reason`` and a replayable history — every
    issued tool call keeps its answer.
    """

    tool_result_limit: int = 50000
    tool_timeout: int = 240
    max_iterations: int = 0  # 0 = unlimited
    max_tool_calls: int = 0  # 0 = unlimited; model-origin calls, this turn
    max_turn_seconds: int = 0  # 0 = unlimited; wall clock, this turn

    def replace(self, **changes) -> AgentConfig:
        """Return a copy with the given fields changed (e.g. for derived agents)."""
        return replace(self, **changes)


@dataclass
class LoopResult:
    """What a turn adds up to — the non-streaming view of the same run.

    Returned by :meth:`AgentLoop.chat`, which is a convenience over
    :meth:`start <AgentLoop.start>` for a caller that wants the answer and
    does not want to drive an event loop of its own. ``messages`` is the
    conversation as of the end of the turn, so the next caller can continue
    without re-reading the state; ``had_error`` says the turn failed rather
    than the answer being empty.
    """

    content: str = ""
    tool_calls_made: int = 0
    messages: list[dict] = field(default_factory=list)
    had_error: bool = False


class IterationLimit(Exception):
    """The turn hit ``AgentConfig.max_iterations`` before the model answered.

    Raised by :meth:`AgentLoop.chat` — a caller asking for "the reply" must
    not mistake an empty string for one. Readers of the event stream see the
    same ending as ``RunFinished(stop_reason="max_iterations")`` instead.
    """

    def __init__(self, iterations: int):
        self.iterations = iterations
        super().__init__(
            f"stopped after {iterations} iterations (AgentConfig.max_iterations)"
        )


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
        #: The one execution path for a tool call — hook interception, the
        #: switched-off check, timeout and cooperative cancellation, status
        #: mapping, truncation, Started/Finished events. The loop runs every
        #: model-origin call through it; anything driving tools of its own
        #: (a plugin, a sub-agent tool) calls it with ``origin="program"`` and
        #: gets the identical pipeline. See :mod:`mocode.core.dispatch`.
        self.dispatcher = ToolDispatcher(
            registry=self._tools,
            hooks=self.hooks,
            config=self.config,
            publish=self._dispatch_publish,
        )
        self._turn: Turn | None = None
        self._idle_state = RunState()
        self._run_id = ""
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
        so does an iteration cap: hitting ``max_iterations`` raises
        :class:`IterationLimit` rather than returning an empty string that
        could be mistaken for the model's answer. Cancelling this coroutine
        stops the turn.
        """
        turn = self.start(user_input)
        try:
            terminal = await turn.wait()
        except asyncio.CancelledError:
            turn.cancel()
            raise
        if isinstance(terminal, RunFailed):
            raise turn.failure or RuntimeError(terminal.error)
        if terminal.stop_reason == "max_iterations":
            raise IterationLimit(terminal.iterations)
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
            self.messages.append(assistant_message(self.INTERRUPT_MSG))
            if turn is not None:
                turn.cancelled = True
            terminal = await self._publish(
                RunFinished(
                    content="",
                    stop_reason="cancelled",
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
        stop_reason: StopReason = "completed"
        started = time.monotonic()
        # The wall clock the provider calls answer to: handed to the retry
        # orchestrator so a budget cut cannot be delayed by a backoff sleep.
        # None means unlimited — no deadline reaches the orchestrator at all.
        deadline = (
            started + self.config.max_turn_seconds
            if self.config.max_turn_seconds > 0
            else None
        )

        await self._publish(
            RunStarted(model=self.model.name, tools=self._tools.names())
        )

        while True:
            # Budget checkpoints, before the next provider call. A turn a
            # budget cuts ends like any other: its terminal event says why,
            # and the history stays replayable — every issued tool call keeps
            # its answer; the call that would have finished a reply never
            # happens. The wall clock is enforced inside retry backoff too:
            # the deadline handed to with_retry_stream stops any sleep or
            # retry past it (RetryDeadlineExceeded lands on the same
            # time_budget path below).
            if (
                self.config.max_iterations > 0
                and iteration >= self.config.max_iterations
            ):
                stop_reason = "max_iterations"
                break
            if (
                self.config.max_tool_calls > 0
                and self.state.tool_calls_made >= self.config.max_tool_calls
            ):
                stop_reason = "max_tool_calls"
                break
            if (
                self.config.max_turn_seconds > 0
                and time.monotonic() - started >= self.config.max_turn_seconds
            ):
                stop_reason = "time_budget"
                break

            iteration += 1
            ctx.messages = self.messages
            ctx.iteration = iteration
            ctx.system_prompt = self.system_prompt

            await self.hooks.before_iteration(ctx)
            self.messages = ctx.messages
            self.system_prompt = ctx.system_prompt

            await self._publish(IterationStarted(iteration=iteration))

            # The request as it is about to be sent — the last interception
            # point. The schema list is a copy: an in-place edit by a hook
            # reaches this one request, never the registry's cache or a
            # frozen payload.
            request_tools = list(self._tools.all_schemas())
            request = RequestContext(
                messages=self.messages,
                system_prompt=self.system_prompt,
                tools=request_tools,
                model=self.model,
                emit=self._emit,
            )
            await self.hooks.before_request(request)
            self.messages = request.messages
            self.system_prompt = request.system_prompt

            acc = StreamAccumulator()
            # index -> call_id, minted at a slot's first fragment: the identity the
            # events announce is the identity the dispatch below will use.
            call_ids: dict[int, str] = {}
            try:
                async for chunk in with_retry_stream(
                    self.provider,
                    self.messages,
                    self.system_prompt,
                    request_tools,
                    self.model.max_tokens,
                    self.model.effort,
                    deadline=deadline,
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
                    for delta in chunk.tool_calls:
                        if delta.index not in call_ids:
                            call_ids[delta.index] = (
                                self.dispatcher.mint_model_call_id(delta.id)
                            )
                        await self._publish(
                            ToolCallArgsDelta(
                                call_id=call_ids[delta.index],
                                name=delta.name,
                                arguments=delta.arguments,
                            )
                        )
            except RetryDeadlineExceeded:
                # The budget ran out inside retry backoff — the same budget
                # endgame as the checkpoints above, not a provider failure
                # and not a cancellation. The history needs nothing from
                # this iteration: the orchestrator raises only before the
                # first chunk (the retry window closes there), so no
                # response took shape, no assistant message enters the
                # history, and every earlier tool call keeps its answer —
                # the transcript replays untouched.
                stop_reason = "time_budget"
                break

            response = acc.build()

            # The fragments already announced each call's identity; the accumulator
            # only merged the wire. Force the ids the events carry onto the calls the
            # dispatcher is about to run, so one identity covers all three phases.
            for index, call in enumerate(response.tool_calls or []):
                call.id = call_ids[index]

            # The response as accounted for — a usage rewrite here flows into
            # IterationFinished and the turn's totals.
            answered = ResponseContext(
                usage=response.usage,
                finish_reason=response.finish_reason,
                iteration=iteration,
            )
            await self.hooks.after_response(answered)

            await self._publish(
                IterationFinished(
                    iteration=iteration,
                    usage=answered.usage,
                    stop_reason=response.finish_reason,
                )
            )

            if response.tool_calls:
                assistant = assistant_message(
                    response.content or "",
                    tool_calls=tool_call_dicts(response.tool_calls),
                    reasoning=response.reasoning_content,
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
            else:
                self.messages.append(
                    assistant_message(
                        response.content or "",
                        reasoning=response.reasoning_content,
                    )
                )
                answer = response.content or ""
                break

        return await self._publish(
            RunFinished(
                content=answer,
                usage=self.state.usage,
                iterations=iteration,
                tool_calls_made=self.state.tool_calls_made,
                stop_reason=stop_reason,
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
        tasks = [
            asyncio.create_task(self._dispatch_model_call(call)) for call in tool_calls
        ]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for call, result in zip(tool_calls, results):
                if isinstance(result, BaseException):
                    out.append(tool_result(call.id, f"{ERROR_PREFIX} {result}"))
                else:
                    out.append(result)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch_model_call(self, call: ToolCall) -> dict:
        """One model-origin call through the dispatcher, as a tool message."""
        args, parse_error = self._parse_args(call)
        result = await self.dispatcher.run(
            call.name, args, call_id=call.id, parse_error=parse_error
        )
        return tool_result(call.id, result.content)

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

    # ---- Helpers ----

    async def _emit(self, event: Event) -> None:
        """Sink for hooks and tools — publish an event on the run's behalf."""
        await self._publish(event)

    async def _dispatch_publish(self, event: Event, *, fold: bool) -> None:
        """The dispatcher's sink: stamp the event, fold it when it is the run's.

        Model-origin events fold into the turn's live state — the run's own
        story. Program-origin ones (nested calls a plugin made on its own
        behalf) are stamped with the same run id and reach every reader of the
        channel, but the live state stays the model's side of the story, so
        they are sent unfolded.
        """
        event.run_id = self._run_id
        if fold and self._turn is not None:
            self._turn.state.apply(event)
        await self.channel.publish(event)

    async def _publish(self, event: Event) -> Event:
        """Stamp an event, fold it into the turn's state, and publish it.

        Every event a run produces goes through here, so the channel carries the
        whole run and ``state`` is already up to date by the time an inline
        subscriber (a hook) looks at it.
        """
        await self._dispatch_publish(event, fold=True)
        return event

    def _interrupt_results(self, tool_calls: list[ToolCall]) -> list[dict]:
        return [tool_result(t.id, self.INTERRUPT_TOOL_MSG) for t in tool_calls]

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
    def tool_call_count(self) -> int:
        """Tool calls started in the current or last turn."""
        return self.state.tool_calls_made
