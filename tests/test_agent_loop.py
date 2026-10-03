"""The agent loop: event stream, tool execution, interception, derive."""

from __future__ import annotations

import asyncio
import threading

import pytest

from mocode.core.agent import AgentConfig, IterationLimit
from mocode.core.events import (
    Event,
    Notice,
    ReasoningDelta,
    RunFailed,
    RunFinished,
    RunStarted,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
)
from mocode.core.hook import (
    AgentHook,
    IterationContext,
    RequestContext,
    ResponseContext,
    ToolCallContext,
)
from mocode.core.provider import Response, RetryPolicy, ToolCall, Usage
from mocode.core.state import DONE, RunState
from mocode.core.tool import ERROR_PREFIX, TIMEOUT_PREFIX, Tool, ToolError, ToolResult
from mocode.testing import (
    MockProvider,
    SlowProvider,
    collect,
    response_to_chunks,
    say,
    tool_call_response,
)

from .conftest import echo_tool, make_agent




# ── the event stream ────────────────────────────────────────


class TestEventStream:
    async def test_run_shape_without_tools(self):
        agent = make_agent(provider=MockProvider([say("hello")]))

        events = await collect(agent.stream("hi"))

        assert [e.type for e in events] == [
            "run_started",
            "iteration_started",
            "text_delta",
            "iteration_finished",
            "run_finished",
        ]
        assert events[-1].content == "hello"
        # 一个 turn 一个 run_id，seq 从 1 起连续编号。
        assert len({e.run_id for e in events}) == 1
        assert [e.seq for e in events] == list(range(1, len(events) + 1))

    async def test_text_and_reasoning_arrive_separately_and_incrementally(self):
        agent = make_agent(provider=MockProvider([say("abc")], chunk_size=1))

        events = await collect(agent.stream("hi"))

        assert [e.text for e in events if isinstance(e, TextDelta)] == ["a", "b", "c"]

        agent = make_agent(
            provider=MockProvider(
                [Response(content="a", reasoning_content="why", usage=Usage(1, 1))]
            )
        )

        events = await collect(agent.stream("hi"))

        assert [e.text for e in events if isinstance(e, ReasoningDelta)] == ["why"]
        assert [e.text for e in events if isinstance(e, TextDelta)] == ["a"]

    async def test_tool_events_share_a_call_id(self):
        agent = make_agent(echo_tool())
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("done"),
        ]

        events = await collect(agent.stream("hi"))

        started = [e for e in events if isinstance(e, ToolCallStarted)]
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        assert [e.call_id for e in started] == [e.call_id for e in finished] == ["c1"]
        assert started[0].args == {"value": "x"}
        assert finished[0].status == "ok"
        assert finished[0].duration >= 0

    async def test_run_finished_summarises_the_turn(self):
        agent = make_agent(echo_tool())
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("final"),
        ]

        events = await collect(agent.stream("hi"))

        done = events[-1]
        assert isinstance(done, RunFinished)
        assert (done.content, done.iterations, done.tool_calls_made) == ("final", 2, 1)
        assert done.usage.prompt_tokens == 2

    async def test_events_serialise_to_plain_data(self):
        agent = make_agent(provider=MockProvider([say("hi")]))

        events = await collect(agent.stream("hi"))

        data = events[0].to_dict()
        assert data["type"] == "run_started"
        assert set(data) == {"type", "run_id", "seq", "model", "tools"}

    async def test_cancellation_leaves_the_history_answerable(self):
        started = asyncio.Event()
        held = asyncio.Event()

        class Stuck(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                started.set()  # the call is in flight, held open below
                await held.wait()  # 挂起等一个永不到达的事件，取消来时才散

        agent = make_agent(echo_tool(), hooks=[Stuck()])
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}'), say("done")]

        async def consume():
            async for _ in agent.stream("hi"):
                pass

        task = asyncio.ensure_future(consume())
        # Cancel against the tool being held in the hook — an event the hook
        # sets when it gets there — not against a sleep that might lose the
        # race on a loaded machine.
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # A reader going away cancels the turn, but the turn tears itself down
        # on its own schedule — wait for the ending it reports.
        terminal = await agent.turn.wait()
        assert terminal.cancelled

        # Every assistant tool call still has an answer, so the history can be
        # sent back to the model on the next turn.
        assistant = next(m for m in agent.messages if m["role"] == "assistant")
        answers = [m for m in agent.messages if m["role"] == "tool"]
        assert len(answers) == len(assistant["tool_calls"])


# ── live state ──────────────────────────────────────────────


class TestRunState:
    async def test_state_is_queryable_while_a_tool_runs(self):
        seen: list[list[str]] = []

        async def _slow(args, ctx):
            seen.append([c.name for c in agent.state.tool_calls.values() if not c.done])
            return "slow done"

        agent = make_agent(
            Tool("slow", "d", {}, _slow, summary_key="", with_context=True),
        )
        agent.provider.responses = [tool_call_response("slow"), say("done")]

        await collect(agent.stream("hi"))

        assert seen == [["slow"]]
        assert agent.state.status == DONE
        assert [c for c in agent.state.tool_calls.values() if not c.done] == []
        assert agent.state.tool_calls_made == 1

    async def test_state_folds_the_same_stream_a_consumer_sees(self):
        agent = make_agent(
            echo_tool(), provider=MockProvider([say("hello")], chunk_size=1)
        )

        mirror = RunState()
        async for event in agent.stream("hi"):
            mirror.apply(event)

        assert mirror.status == DONE
        assert mirror.answer == agent.state.answer == "hello"
        assert mirror.content == agent.state.content
        assert mirror.usage == agent.state.usage


# ── tool execution ──────────────────────────────────────────


def _failing(exc: Exception) -> Tool:
    def _run(args):
        raise exc

    return Tool("boom", "d", {}, _run)


def _boom_tool() -> Tool:
    def broken(args):
        raise ToolError("it broke", "custom_code")

    return Tool("boom", "b", {}, broken)


def _parking_tool() -> Tool:
    """A tool that waits for the loop's cancel signal instead of sleeping.

    "Real work that outlasts the timeout" is expressed as waiting for the
    cooperative signal, so the loop's own timer decides the timeout — and
    the test carries no sleep of its own. The worker stands down the moment
    the signal arrives.
    """

    def patient(args, ctx):
        ctx.cancel_event.wait(timeout=5.0)
        return "late"

    return Tool("patient", "waits politely", {}, patient, with_context=True)


class TestToolExecution:
    @pytest.mark.parametrize(
        "make,tool_timeout,expected,error_code",
        [
            (lambda: _failing(RuntimeError("kaboom")), 5, "error", None),
            (lambda: _failing(ToolError("nope", "teapot")), 5, "error", "teapot"),
            # Real work that outlasts the timeout: the loop's own timer —
            # not the tool — decides the outcome, and the worker is told to
            # stand down the moment it fires.
            (_parking_tool, 0.05, "timeout", None),
        ],
    )
    async def test_failure_statuses(self, make, tool_timeout, expected, error_code):
        tool = make()
        seen: list[tuple[str, str | None]] = []

        class Recorder(AgentHook):
            async def on_event(self, event: Event) -> None:
                if isinstance(event, ToolCallFinished):
                    seen.append((event.status, event.error_code))

        agent = make_agent(tool, hooks=[Recorder()], config=AgentConfig(tool_timeout=tool_timeout))
        agent.provider.responses = [tool_call_response(tool.name), say("done")]

        await collect(agent.stream("hi"))

        # 状态与错误码集中在这里断言：每条失败都有一个可区分的 code。
        assert seen == [(expected, error_code)]

    async def test_malformed_arguments_become_an_error_result(self):
        agent = make_agent(echo_tool())
        agent.provider.responses = [tool_call_response("echo", "{not json"), say("done")]

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.status == "error"
        assert finished.result.startswith("error:")
        assert "invalid JSON" in finished.result

    async def test_a_batch_runs_concurrently_and_reports_each_call(self):
        """The two calls ran together — proven by construction, not a clock.

        The second call releases the first, so a loop that ran them back to
        back could never finish this test. (A wall-clock "elapsed was under
        the sum" could only ever hope the machine was fast enough.)
        """
        sibling = asyncio.Event()

        async def _slow(args, ctx):
            await sibling.wait()  # the other call is running before this ends
            return args["value"]

        def _pong(args):
            sibling.set()
            return f"echo:{args['value']}"

        schema = {"type": "object", "properties": {"value": {"type": "string"}}}
        agent = make_agent(
            Tool("echo", "d", schema, _pong),
            Tool("slow", "d", schema, _slow, with_context=True),
        )
        agent.provider.responses = [
            Response(
                tool_calls=[
                    ToolCall(id="c1", name="slow", arguments='{"value": "a"}'),
                    ToolCall(id="c2", name="echo", arguments='{"value": "b"}'),
                ],
                usage=Usage(1, 1),
                finish_reason="tool_calls",
            ),
            say("done"),
        ]

        # Bounded: a sequential loop would deadlock on the wait above, so the
        # bound is what turns that hang into a plain failure.
        events = await asyncio.wait_for(collect(agent.stream("hi")), 5.0)

        assert sibling.is_set()
        results = {
            e.call_id: e.result for e in events if isinstance(e, ToolCallFinished)
        }
        assert results == {"c1": "a", "c2": "echo:b"}

    async def test_a_streaming_tool_reports_output(self):
        async def _chatty(args, ctx):
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text="line 1\n"))
            await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text="line 2\n"))
            return "line 1\nline 2\n"

        agent = make_agent(Tool("chatty", "d", {}, _chatty, with_context=True))
        agent.provider.responses = [tool_call_response("chatty"), say("done")]

        events = await collect(agent.stream("hi"))

        outputs = [e.text for e in events if isinstance(e, ToolOutput)]
        assert outputs == ["line 1\n", "line 2\n"]
        assert agent.state.tool_calls["c1"].output_text == "line 1\nline 2\n"

    async def test_a_tool_may_return_structured_details(self):
        agent = make_agent(
            Tool("stats", "d", {}, lambda a: ToolResult("read it", {"lines": 412}))
        )
        agent.provider.responses = [tool_call_response("stats"), say("done")]

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.result == "read it"          # what the model reads
        assert finished.details == {"lines": 412}    # what a frontend reads
        assert agent.state.tool_calls["c1"].details == {"lines": 412}
        # …and the details stay out of the conversation
        tool_msg = next(m for m in agent.messages if m["role"] == "tool")
        assert tool_msg["content"] == "read it"

        # A plain string result carries no details — the two channels only
        # part ways when something structured actually came back.
        agent = make_agent(echo_tool())
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("done"),
        ]

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.details == {}

    async def test_a_hook_may_enrich_details(self):
        class Enricher(AgentHook):
            async def on_tool_complete(self, ctx: ToolCallContext) -> None:
                ctx.tool_details["audited"] = True

        agent = make_agent(echo_tool(), hooks=[Enricher()])
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}'), say("done")]

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.details == {"audited": True}


# ── interception ────────────────────────────────────────────


class TestInterception:
    async def test_deny_vetoes_execution(self):
        executed = []

        class Denier(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                ctx.deny = "not allowed here"

        tool = Tool("risky", "d", {"type": "object", "properties": {"value": {"type": "string"}}},
                    lambda a: executed.append(a) or "ran")
        agent = make_agent(tool, hooks=[Denier()])
        agent.provider.responses = [tool_call_response("risky", '{"value": "x"}'), say("done")]

        events = await collect(agent.stream("hi"))

        assert executed == []
        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.status == "denied"
        assert finished.result.startswith("denied:")

    async def test_hooks_may_rewrite_args_and_results(self):
        class Rewriter(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                ctx.tool_args["value"] = "rewritten"

            async def on_tool_complete(self, ctx: ToolCallContext) -> None:
                ctx.tool_result = f"[{ctx.tool_result}]"

        agent = make_agent(echo_tool(), hooks=[Rewriter()])
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "original"}'),
            say("done"),
        ]

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.result == "[echo:rewritten]"
        # The event reports final arguments, so a consumer never sees stale ones.
        started = next(e for e in events if isinstance(e, ToolCallStarted))
        assert started.args == {"value": "rewritten"}

    async def test_before_iteration_may_rewrite_the_system_prompt(self):
        class Persona(AgentHook):
            async def before_iteration(self, ctx: IterationContext) -> None:
                ctx.system_prompt = "persona"

        agent = make_agent(echo_tool(), hooks=[Persona()])
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}'), say("done")]

        await collect(agent.stream("hi"))

        # 首/末请求都带着改写后的提示词（本 turn 的两个请求）。
        systems = [c["system"] for c in agent.provider.calls]
        assert set(systems) == {"persona"} and len(systems) == 2
        # The rewrite is scoped to the run: the conversation keeps its prompt.
        assert agent.system_prompt == "sys"

    async def test_a_system_prompt_rewrite_does_not_leak_into_the_next_turn(self):
        """What before_iteration changed lasts the run, not the conversation."""

        class Injects(AgentHook):
            def __init__(self) -> None:
                self.done = False

            async def before_iteration(self, ctx: IterationContext) -> None:
                if not self.done:
                    ctx.system_prompt += " +injected"
                    self.done = True

        agent = make_agent(echo_tool(), hooks=[Injects()])
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("done"),
            say("done"),
        ]

        await collect(agent.stream("hi"))
        await collect(agent.stream("again"))

        systems = [c["system"] for c in agent.provider.calls]
        # 注入一次，本 turn 的每个请求都带着它——
        assert set(systems[:-1]) == {"sys +injected"}
        # ——而第二个 turn 从当时存储的提示词起，注入不渗过去。
        assert systems[-1] == "sys"

    async def test_before_iteration_may_rewrite_messages(self):
        seen: list[int] = []

        class Trimmer(AgentHook):
            async def before_iteration(self, ctx: IterationContext) -> None:
                seen.append(ctx.iteration)
                if ctx.iteration == 2:
                    ctx.messages[:] = [ctx.messages[0]]

        agent = make_agent(echo_tool(), hooks=[Trimmer()])
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}'), say("done")]

        await collect(agent.stream("hi"))

        assert seen == [1, 2]
        # 末请求（裁剪后的那次）只剩用户消息：首条即末条，同为 user。
        last = agent.provider.calls[-1]
        assert [m["role"] for m in last["messages"]] == ["user"]


# ── stop reasons and budgets ────────────────────────────────


class TestStopReasons:
    """Five endings, one distinguishable field — and a replayable history."""

    async def test_a_completed_turn_says_so(self):
        agent = make_agent(provider=MockProvider([say("done")]))
        events = await collect(agent.stream("hi"))
        assert events[-1].stop_reason == "completed"
        assert events[-1].cancelled is False

    async def test_max_iterations_reports_its_stop_reason(self):
        agent = make_agent(echo_tool(), config=AgentConfig(max_iterations=2))
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        events = await collect(agent.stream("hi"))

        assert events[-1].stop_reason == "max_iterations"
        assert events[-1].content == ""
        assert events[-1].iterations == 2
        assert events[-1].to_dict()["stop_reason"] == "max_iterations"
        # 计数与事件流同源：两次 iteration_started，不多不少。
        assert sum(1 for e in events if e.type == "iteration_started") == 2

    async def test_a_tool_call_budget_ends_the_turn(self):
        agent = make_agent(echo_tool(), config=AgentConfig(max_tool_calls=1))
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        events = await collect(agent.stream("hi"))

        assert events[-1].stop_reason == "max_tool_calls"
        assert events[-1].iterations == 1
        assert events[-1].tool_calls_made == 1
        # The cut history is replayable: the one call that ran is answered.
        assistant = next(m for m in agent.messages if m.get("tool_calls"))
        answers = [m for m in agent.messages if m["role"] == "tool"]
        assert len(answers) == len(assistant["tool_calls"])

    async def test_a_wall_clock_budget_ends_the_turn(self, monkeypatch):
        import mocode.core.agent as agent_module
        import mocode.core.provider as provider_module

        class FastClock:
            """Ten seconds pass after the third read: the turn-start read,
            the first checkpoint and the orchestrator's attempt-top check all
            agree (within budget, the provider call goes out), and the next
            checkpoint — iteration two's top — is already past it."""

            def __init__(self) -> None:
                self.now = 1000.0
                self.reads = 0

            def monotonic(self) -> float:
                self.reads += 1
                if self.reads > 3:
                    self.now += 10.0
                return self.now

        clock = FastClock()
        monkeypatch.setattr(agent_module, "time", clock)
        # The deadline reaches the retry orchestrator, so it reads the same
        # clock the loop does.
        monkeypatch.setattr(provider_module, "time", clock)
        agent = make_agent(echo_tool(), config=AgentConfig(max_turn_seconds=5))
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        events = await collect(agent.stream("hi"))

        assert events[-1].stop_reason == "time_budget"
        assert events[-1].iterations == 1

    async def test_a_budget_cut_inside_retry_backoff_is_not_a_failure(
        self, monkeypatch
    ):
        """The wall clock bounds the backoff itself: two retriable failures
        burn the budget, and the turn ends time_budget — not failed, not
        cancelled — even though the provider would have answered next."""

        import mocode.core.agent as agent_module
        import mocode.core.provider as provider_module

        class Clock:
            def __init__(self) -> None:
                self.now = 0.0

            def monotonic(self) -> float:
                return self.now

        rate = type("RateLimitError", (Exception,), {})

        class BurningProvider(MockProvider):
            """Each attempt consumes simulated wall clock before failing."""

            def __init__(self, responses, clock, burn: float):
                super().__init__(responses)
                self._clock = clock
                self._burn = burn

            def is_retriable(self, exc: Exception) -> bool:
                return isinstance(exc, rate)

            async def stream(self, messages, system, tools, max_tokens, effort):
                self.calls.append({"messages": list(messages)})
                self._clock.now += self._burn
                outcome = self.responses.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                async for chunk in response_to_chunks(outcome):
                    yield chunk

        clock = Clock()
        monkeypatch.setattr(agent_module, "time", clock)
        monkeypatch.setattr(provider_module, "time", clock)
        provider = BurningProvider(
            [rate("429"), rate("429"), say("late")], clock, burn=3.0
        )
        # Real (unpatched) sleeps, but milliseconds — the budget is burned by
        # the attempts, not the backoff.
        provider.retry_policy = RetryPolicy(base_delay=0.001, jitter=0.0)
        agent = make_agent(
            provider=provider, config=AgentConfig(max_turn_seconds=5)
        )

        events = await collect(agent.stream("hi"))

        assert events[-1].stop_reason == "time_budget"
        assert events[-1].iterations == 1
        assert not any(isinstance(e, RunFailed) for e in events)
        assert len(provider.calls) == 2  # the third attempt never happened
        # The cut history is replayable: the orchestrator raises before the
        # first chunk, so no response took shape and no assistant message
        # entered the history mid-iteration.
        assert agent.messages == [{"role": "user", "content": "hi"}]

    async def test_cancelled_is_a_stop_reason(self):
        entered = asyncio.Event()
        agent = make_agent(provider=_slow_provider(entered))
        turn = agent.start("hi")
        await asyncio.wait_for(entered.wait(), 5)  # the request went out
        turn.cancel()

        terminal = await turn.wait()

        assert terminal.stop_reason == "cancelled"
        assert terminal.cancelled is True

    async def test_chat_raises_iteration_limit_instead_of_answering_empty(self):
        agent = make_agent(echo_tool(), config=AgentConfig(max_iterations=2))
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        with pytest.raises(IterationLimit) as exc:
            await agent.chat("go")

        assert exc.value.iterations == 2
        assert agent.state.status == DONE  # a budget cut is an ending, not a failure

    async def test_run_with_messages_reports_the_limit_as_an_error_result(self):
        agent = make_agent(echo_tool(), config=AgentConfig(max_iterations=1))
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        result = await agent.run_with_messages([{"role": "user", "content": "hi"}])

        assert result.had_error is True
        assert "iterations" in result.content


# ── request interception ────────────────────────────────────


class TestRequestInterception:
    async def test_before_request_may_rewrite_the_messages(self):
        class Rewriter(AgentHook):
            async def before_request(self, ctx: RequestContext) -> None:
                ctx.messages = [
                    {"role": "user", "content": "replaced before the wire"}
                ]

        agent = make_agent(provider=MockProvider([say("done")]), hooks=[Rewriter()])

        await collect(agent.stream("hi"))

        # 唯一一次请求：一条 user 消息，内容是钩子换上的。
        (only,) = agent.provider.calls
        assert [m["role"] for m in only["messages"]] == ["user"]
        assert only["messages"][0]["content"] == "replaced before the wire"

    async def test_before_request_prompt_rewrite_is_scoped_to_the_run(self):
        class Persona(AgentHook):
            async def before_request(self, ctx: RequestContext) -> None:
                ctx.system_prompt = "on the wire"

        agent = make_agent(provider=MockProvider([say("done")]), hooks=[Persona()])

        await collect(agent.stream("hi"))

        assert [c["system"] for c in agent.provider.calls] == ["on the wire"]
        assert agent.system_prompt == "sys"  # the conversation keeps its prompt

    async def test_the_tools_snapshot_is_what_the_request_carries(self):
        seen: list[dict] = []

        class Watcher(AgentHook):
            async def before_request(self, ctx: RequestContext) -> None:
                seen.append(ctx.tools)

        agent = make_agent(echo_tool(), provider=MockProvider([say("done")]), hooks=[Watcher()])

        await collect(agent.stream("hi"))

        # 钩子看到的快照就是唯一那次请求带走的快照。
        (only,) = agent.provider.calls
        assert seen == [only["tools"]]
        assert [s["function"]["name"] for s in seen[0]] == ["echo"]

    async def test_an_in_place_tools_edit_reaches_this_request_alone(self):
        class Injector(AgentHook):
            async def before_request(self, ctx: RequestContext) -> None:
                ctx.tools.append({"type": "function", "function": {"name": "ghost"}})

        agent = make_agent(echo_tool(), provider=MockProvider([say("done")]), hooks=[Injector()])

        await collect(agent.stream("hi"))

        # 唯一一次请求带着 ghost 一起走。
        (only,) = agent.provider.calls
        sent = [s["function"]["name"] for s in only["tools"]]
        assert sent == ["echo", "ghost"]
        # The registry — including anything frozen — never saw the ghost.
        assert [s["function"]["name"] for s in agent.tool_registry.all_schemas()] == ["echo"]

    async def test_after_response_may_correct_the_usage(self):
        class Auditor(AgentHook):
            async def after_response(self, ctx: ResponseContext) -> None:
                ctx.usage = Usage(10, 20)

        agent = make_agent(provider=MockProvider([say("done")]), hooks=[Auditor()])

        events = await collect(agent.stream("hi"))

        iteration = next(e for e in events if e.type == "iteration_finished")
        assert (iteration.usage.prompt_tokens, iteration.usage.completion_tokens) == (10, 20)
        assert events[-1].usage.prompt_tokens == 10  # the turn's totals add up

    async def test_after_response_sees_the_finish_reason_and_iteration(self):
        seen: list[tuple[str | None, int]] = []

        class Watcher(AgentHook):
            async def after_response(self, ctx: ResponseContext) -> None:
                seen.append((ctx.finish_reason, ctx.iteration))

        agent = make_agent(echo_tool(), hooks=[Watcher()])
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("done"),
        ]

        await collect(agent.stream("hi"))

        assert seen == [("tool_calls", 1), ("stop", 2)]

    async def test_a_raising_interception_hook_does_not_break_the_turn(self):
        class Bad(AgentHook):
            async def before_request(self, ctx: RequestContext) -> None:
                raise RuntimeError("boom")

            async def after_response(self, ctx: ResponseContext) -> None:
                raise RuntimeError("bam")

        agent = make_agent(provider=MockProvider([say("still fine")]), hooks=[Bad()])

        events = await collect(agent.stream("hi"))

        assert events[-1].content == "still fine"

    async def test_before_request_runs_after_before_iteration(self):
        order: list[str] = []

        class Both(AgentHook):
            async def before_iteration(self, ctx: IterationContext) -> None:
                order.append("iteration")

            async def before_request(self, ctx: RequestContext) -> None:
                order.append("request")

        agent = make_agent(provider=MockProvider([say("done")]), hooks=[Both()])

        await collect(agent.stream("hi"))

        assert order == ["iteration", "request"]


# ── hook event channel ──────────────────────────────────────


class TestEventChannel:
    async def test_plugin_events_reach_both_hooks_and_the_stream(self):
        class Plugin(AgentHook):
            async def before_iteration(self, ctx: IterationContext) -> None:
                await ctx.emit(Notice(message="hello from a plugin"))

            async def on_event(self, event: Event) -> None:
                if isinstance(event, Notice):
                    seen.append(event.message)

        seen: list[str] = []
        agent = make_agent(provider=MockProvider([say("done")]), hooks=[Plugin()])

        events = await collect(agent.stream("hi"))

        assert seen == ["hello from a plugin"]
        assert [e.message for e in events if isinstance(e, Notice)] == [
            "hello from a plugin"
        ]

    async def test_tool_hooks_can_emit(self):
        class Emitter(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                await ctx.emit(Notice(message=f"tool {ctx.tool_name}"))

        agent = make_agent(echo_tool(), hooks=[Emitter()])
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}'), say("done")]

        events = await collect(agent.stream("hi"))

        notices = [e for e in events if isinstance(e, Notice)]
        assert [n.message for n in notices] == ["tool echo"]
        # Emitted from on_tool_start, so it precedes the call it is about.
        assert events.index(notices[0]) < next(
            i for i, e in enumerate(events) if isinstance(e, ToolCallStarted)
        )


# ── derive ──────────────────────────────────────────────────


class TestDerive:
    def test_inherits_copies_not_shared_mutable_state(self):
        parent = make_agent(echo_tool())
        parent.messages.append({"role": "user", "content": "old"})

        child = parent.derive()

        assert child.provider is parent.provider
        assert child.system_prompt == parent.system_prompt
        assert child.messages == []
        # The capability set is inherited, the management surface is not:
        # a child that disables or registers reaches nothing of the parent's.
        assert child.tool_registry is not parent.tool_registry
        assert child.tool_registry.names() == parent.tool_registry.names()
        assert child.config == parent.config
        assert child.config is not parent.config

    def test_child_registry_changes_do_not_reach_the_parent(self):
        parent = make_agent(echo_tool("a"), echo_tool("b"))

        child = parent.derive()
        child.tool_registry.disable("a")
        child.tool_registry.register(echo_tool("c"))

        assert child.tool_registry.names() == ["b", "c"]
        assert parent.tool_registry.names() == ["a", "b"]

    def test_provider_override(self):
        parent = make_agent(echo_tool())

        replacement = MockProvider([], model="cheap-model")
        child = parent.derive(provider=replacement)

        assert child.provider is replacement
        assert parent.provider is not replacement

    def test_overrides_are_independent(self):
        parent = make_agent(echo_tool("a"), echo_tool("b"))

        child = parent.derive(
            system_prompt="child",
            tools=parent.tool_registry.select(include_names={"a"}),
            config=parent.config.replace(max_iterations=3),
        )

        assert child.system_prompt == "child"
        assert child.tool_registry.names() == ["a"]
        assert child.config.max_iterations == 3
        assert child.config.tool_timeout == parent.config.tool_timeout
        assert parent.system_prompt == "sys"
        assert parent.tool_registry.names() == ["a", "b"]

    async def test_child_runs_without_touching_the_parent(self):
        parent = make_agent(echo_tool(), provider=MockProvider([say("child answer")]))

        result = await parent.derive().run_with_messages(
            [{"role": "user", "content": "hi"}]
        )

        assert result.content == "child answer"
        assert result.had_error is False
        assert parent.messages == []


# ── chat convenience wrapper ────────────────────────────────


class TestChat:
    async def test_returns_the_final_answer_and_records_history(self):
        agent = make_agent(provider=MockProvider([say("Hi!")]))

        assert await agent.chat("hello") == "Hi!"
        assert [m["role"] for m in agent.messages] == ["user", "assistant"]

    async def test_provider_failure_raises(self):
        class Dead(MockProvider):
            async def stream(self, *args):
                raise RuntimeError("upstream is down")
                yield  # pragma: no cover — makes this a generator

        agent = make_agent(provider=Dead())

        with pytest.raises(RuntimeError, match="upstream is down"):
            await agent.chat("hello")

        assert agent.state.status == "failed"

    async def test_run_with_messages_reports_errors_instead_of_raising(self):
        class Dead(MockProvider):
            async def stream(self, *args):
                raise RuntimeError("nope")
                yield  # pragma: no cover

        agent = make_agent(provider=Dead())

        result = await agent.run_with_messages([{"role": "user", "content": "hi"}])

        assert result.had_error is True
        assert "nope" in result.content


# ── turns: addressable, watched by many, cancellable ────────


def _slow_provider(entered: asyncio.Event | None = None) -> SlowProvider:
    """A provider whose turn never finishes on its own.

    The park itself is the kit's :class:`SlowProvider` — cancellable, and
    bounded by every caller's ``wait_for``. *entered*, when given, is set
    the moment the request goes out: the "the request is genuinely in
    flight" signal a cancel test synchronizes on, so the cancellation meets
    a request under way rather than a sleep of comparable length that might
    lose the race.
    """

    class Announced(SlowProvider):
        async def stream(self, *args):
            if entered is not None:
                entered.set()
            async for chunk in super().stream(*args):
                yield chunk

    return Announced()


class TestTurns:
    async def test_a_turn_refuses_to_start_while_one_is_running(self):
        agent = make_agent(provider=_slow_provider())

        # No await needed: start() is a plain call, so the refusal is immediate
        # rather than surfacing on the first read of a stream.
        turn = agent.start("hi")
        assert agent.busy
        with pytest.raises(RuntimeError, match="already running"):
            agent.start("again")

        turn.cancel()
        await turn.wait()

    async def test_a_turn_can_be_watched_after_it_started(self):
        agent = make_agent(provider=MockProvider([say("hello")]))

        turn = agent.start("hi")
        events = await collect(turn.subscribe())

        assert isinstance(events[0], RunStarted)
        assert isinstance(events[-1], RunFinished)
        assert all(event.run_id == turn.id for event in events)

    async def test_two_readers_see_the_same_run(self):
        agent = make_agent(provider=MockProvider([say("hello")], chunk_size=1))

        turn = agent.start("hi")
        first = turn.subscribe()
        second = turn.subscribe()

        watched = await collect(first)
        mirrored = await collect(second)

        assert [e.seq for e in watched] == [e.seq for e in mirrored]
        assert mirrored[-1].content == "hello"

    async def test_the_run_outlives_a_reader_that_walks_away(self):
        agent = make_agent(provider=MockProvider([say("hello")]))

        turn = agent.start("hi")
        sub = turn.subscribe()
        await sub.get()
        sub.close()

        terminal = await turn.wait()
        assert terminal.content == "hello"
        assert not terminal.cancelled

    async def test_giving_up_on_the_wait_does_not_stop_the_turn(self):
        entered = asyncio.Event()
        agent = make_agent(provider=_slow_provider(entered))
        turn = agent.start("hi")

        waiter = asyncio.ensure_future(turn.wait())
        await asyncio.wait_for(entered.wait(), 5)  # the turn is under way
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert agent.busy
        turn.cancel()
        assert (await turn.wait()).cancelled

    async def test_cancelling_ends_the_turn_and_the_agent_runs_again(self):
        entered = asyncio.Event()
        agent = make_agent(provider=_slow_provider(entered))

        turn = agent.start("hi")
        await asyncio.wait_for(entered.wait(), 5)  # the request is in flight
        turn.cancel()
        terminal = await turn.wait()

        assert terminal.cancelled is True
        assert terminal.content == ""
        assert agent.state.status == "cancelled"
        assert not agent.busy

        agent.provider = MockProvider([say("second")])
        assert await agent.chat("again") == "second"

    async def test_a_derived_agent_can_report_into_the_parents_channel(self):
        parent = make_agent(provider=MockProvider([say("from the child")]))
        child = parent.derive(channel=parent.channel)
        reader = parent.channel.subscribe()

        await child.chat("hi")

        seen = []
        while (event := reader.take()) is not None:
            seen.append(event)
        assert any(isinstance(e, TextDelta) for e in seen)
        # The parent's own history is untouched: it was the child's turn.
        assert parent.messages == []
        assert not any(e.run_id == "" for e in seen)


# ── the terminal-event guarantee ───────────────────────────


class TestTerminalEventGuarantee:
    async def test_a_base_exception_still_ends_the_turn_for_readers(self):
        """SystemExit/KeyboardInterrupt must not leave subscribers hanging.

        The turn ends like any failed turn — terminal event published, failure
        parked on the Turn — instead of pushing the exception through the task
        into the event loop, past every reader.
        """

        class Exploding(MockProvider):
            async def stream(self, *args):
                raise KeyboardInterrupt  # a BaseException, not an Exception
                yield  # pragma: no cover — makes this a generator

        agent = make_agent(provider=Exploding())
        turn = agent.start("hi")
        reader = turn.subscribe()

        terminal = await turn.wait()

        assert isinstance(terminal, RunFailed)
        assert isinstance(turn.failure, KeyboardInterrupt)
        events = await collect(reader)
        assert events[-1] is terminal
        assert agent.state.status == "failed"
        assert not agent.busy


# ── cooperative cancellation for sync tools ────────────────


def _patient_tool(started: threading.Event, abandoned: threading.Event) -> Tool:
    """A sync tool that waits for the loop's signal instead of spinning.

    ``started`` is set on entry; ``abandoned`` only once the loop's cancel
    signal actually arrives — so the test waits on observed events, and "the
    worker noticed it was abandoned" is a fact rather than a polled guess.
    """

    def patient(args, ctx):
        started.set()
        if ctx.cancel_event.wait(timeout=5):
            abandoned.set()
        return "finally"

    return Tool("patient", "waits politely", {}, patient, with_context=True)


async def _thread_event(event: threading.Event, timeout: float = 5.0) -> None:
    """Await a flag a worker thread set — a wait, not a poll.

    ``to_thread`` runs the thread's own blocking wait, so the test carries no
    sleep cadence of its own: the moment the thread notices, the test resumes.
    A flag that never comes fails the assertion instead of spinning.
    """
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, timeout), timeout + 5)


class TestSyncToolCancellation:
    async def test_a_timed_out_sync_tool_sees_the_cancel_signal(self):
        started = threading.Event()
        abandoned = threading.Event()
        # A tenth of a second: the loop's timer decides the timeout, and the
        # worker is still inside its wait when it does.
        agent = make_agent(
            _patient_tool(started, abandoned), config=AgentConfig(tool_timeout=0.1)
        )
        agent.provider = MockProvider(
            [tool_call_response("patient"), say("given up waiting")]
        )

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.status == "timeout"
        await _thread_event(started)
        await _thread_event(abandoned)
        assert abandoned.is_set()  # the worker noticed it was abandoned

    async def test_cancelling_the_turn_signals_a_running_sync_tool(self):
        started = threading.Event()
        abandoned = threading.Event()
        agent = make_agent(
            _patient_tool(started, abandoned),
            provider=MockProvider([tool_call_response("patient")]),
        )

        turn = agent.start("hi")
        await _thread_event(started)  # the tool is running — safe to cancel
        turn.cancel()

        assert (await turn.wait()).cancelled is True
        await _thread_event(abandoned)
        assert abandoned.is_set()


# ── the persisted outcome protocol ─────────────────────────


class TestErrorPrefixes:
    """Every failed call's persisted result says so with a prefix.

    A saved session is a message list — the prefix is the only place an
    outcome survives for whoever replays it (see core/tool.py). The status
    and the code are asserted in the one place the outcome is read.
    """

    @pytest.mark.parametrize(
        "name,make,tool_timeout,status,error_code,prefix",
        [
            ("boom", _boom_tool, 5, "error", "custom_code", f"{ERROR_PREFIX} custom_code:"),
            # 没注册的工具：调用发出去了，注册表答不上来。
            ("ghost", lambda: None, 5, "not_found", None, f"{ERROR_PREFIX} unknown tool"),
            ("patient", _parking_tool, 0.05, "timeout", None, TIMEOUT_PREFIX),
        ],
    )
    async def test_the_persisted_result_says_what_happened(
        self, name, make, tool_timeout, status, error_code, prefix
    ):
        tool = make()
        agent = make_agent(tool, config=AgentConfig(tool_timeout=tool_timeout)) if tool else make_agent(config=AgentConfig(tool_timeout=tool_timeout))
        agent.provider = MockProvider(
            [tool_call_response(name), say("moved on")]
        )

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert (finished.status, finished.error_code) == (status, error_code)
        assert finished.result.startswith(prefix)
        # The message the next turn replays carries the same prefix.
        tool_message = next(m for m in agent.messages if m["role"] == "tool")
        assert tool_message["content"].startswith(prefix)
