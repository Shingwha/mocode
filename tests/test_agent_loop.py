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
from mocode.core.tool import (
    DENIED_PREFIX,
    ERROR_PREFIX,
    TIMEOUT_PREFIX,
    Tool,
    ToolError,
    ToolResult,
)
from mocode.testing import (
    MockProvider,
    SlowProvider,
    collect,
    response_to_chunks,
    say,
    tool_call_response,
)

from .conftest import FakeClock, advance, echo_tool, make_agent




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
        # 跑完的 turn：completed 且没有取消标记（其余四种收场各有自己的测试）。
        assert events[-1].stop_reason == "completed"
        assert events[-1].cancelled is False

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

    async def test_a_tool_turn_reports_its_events_and_summary(self):
        agent = make_agent(echo_tool())
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("final"),
        ]

        events = await collect(agent.stream("hi"))

        # 一对 ToolCallStarted/Finished 共用一个 call_id，参数与终态落在事件上。
        started = [e for e in events if isinstance(e, ToolCallStarted)]
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        assert [e.call_id for e in started] == [e.call_id for e in finished] == ["c1"]
        assert started[0].args == {"value": "x"}
        assert finished[0].status == "ok"
        assert finished[0].duration >= 0

        # turn 的总结：两次迭代、一次工具调用，用量随之累计。
        done = events[-1]
        assert isinstance(done, RunFinished)
        assert (done.content, done.iterations, done.tool_calls_made) == ("final", 2, 1)
        assert done.usage.prompt_tokens == 2

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
    async def test_the_live_state_mirrors_the_run(self):
        seen: list[list[str]] = []

        async def _slow(args, ctx):
            seen.append([c.name for c in agent.state.tool_calls.values() if not c.done])
            return "slow done"

        agent = make_agent(
            Tool("slow", "d", {}, _slow, summary_key="", with_context=True),
        )
        agent.provider.responses = [tool_call_response("slow"), say("done")]

        mirror = RunState()
        async for event in agent.stream("hi"):
            mirror.apply(event)

        # 工具跑着的时候，state 已经能回答"哪个调用在飞"。
        assert seen == [["slow"]]
        assert [c for c in agent.state.tool_calls.values() if not c.done] == []
        assert agent.state.tool_calls_made == 1
        # 消费者自己折的镜像与 agent 的 state 收敛到同一个快照。
        assert mirror.status == DONE
        assert mirror.answer == agent.state.answer == "done"
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

    async def test_a_prompt_rewrite_reaches_every_request_and_dies_with_the_run(self):
        """两个钩子点都能改写提示词；改写只活在本次 run，不渗进对话。"""

        class Scoped(AgentHook):
            def __init__(self) -> None:
                self.iteration_done = False
                self.request_done = False

            async def before_iteration(self, ctx: IterationContext) -> None:
                if not self.iteration_done:
                    ctx.system_prompt += " +iteration"
                    self.iteration_done = True

            async def before_request(self, ctx: RequestContext) -> None:
                if not self.request_done:
                    ctx.system_prompt += " +request"
                    self.request_done = True

        agent = make_agent(echo_tool(), hooks=[Scoped()])
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("done"),
            say("done"),
        ]

        await collect(agent.stream("hi"))
        await collect(agent.stream("again"))

        systems = [c["system"] for c in agent.provider.calls]
        # 注入一次，本 turn 的每个请求都带着两处改写的总和——
        assert set(systems[:-1]) == {"sys +iteration +request"}
        # ——而第二个 turn 从当时存储的提示词起，注入不渗过去。
        assert systems[-1] == "sys"
        # The rewrite is scoped to the run: the conversation keeps its prompt.
        assert agent.system_prompt == "sys"

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

    @pytest.mark.parametrize(
        "config,stop_reason,iterations,tool_calls_made",
        [
            # 迭代预算与工具调用预算：两种花法的钱，收场方式一样可判别。
            (AgentConfig(max_iterations=2), "max_iterations", 2, 1),
            (AgentConfig(max_tool_calls=1), "max_tool_calls", 1, 1),
        ],
    )
    async def test_a_budget_ends_the_turn(
        self, config, stop_reason, iterations, tool_calls_made
    ):
        agent = make_agent(echo_tool(), config=config)
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        events = await collect(agent.stream("hi"))

        assert events[-1].stop_reason == stop_reason
        assert events[-1].content == ""
        assert events[-1].iterations == iterations
        assert events[-1].tool_calls_made == tool_calls_made
        assert events[-1].to_dict()["stop_reason"] == stop_reason
        # 计数与事件流同源：几次 iteration_started，不多不少。
        assert sum(1 for e in events if e.type == "iteration_started") == iterations
        # The cut history is replayable: every call that ran is answered.
        asked = [m for m in agent.messages if m.get("tool_calls")]
        answers = [m for m in agent.messages if m["role"] == "tool"]
        assert len(answers) == sum(len(m["tool_calls"]) for m in asked)

    async def test_a_wall_clock_budget_ends_the_turn(self, monkeypatch):
        """The work itself burns past the turn's wall-clock budget.

        接缝说明：产品在模块级读 ``time.monotonic()``（``AgentConfig`` 没有
        时钟注入点），所以替换模块 ``time`` 是测试给假时钟的唯一路径——
        这里用 conftest 的 FakeClock 手推。若产品以后接受注入时钟，这个
        monkeypatch 会随之消失，属预期内的重构。
        """
        import mocode.core.agent as agent_module
        import mocode.core.provider as provider_module

        clock = FakeClock()
        monkeypatch.setattr(agent_module, "time", clock)
        # The deadline reaches the retry orchestrator, so it reads the same
        # clock the loop does.
        monkeypatch.setattr(provider_module, "time", clock)

        def burner(args):
            advance(clock, 10.0)  # 这一步真活把 5s 预算烧穿了
            return "echo:x"

        agent = make_agent(
            Tool("echo", "d", {"type": "object", "properties": {}}, burner),
            config=AgentConfig(max_turn_seconds=5),
        )
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        events = await collect(agent.stream("hi"))

        # 预算内的请求照发；下一步 checkpoint 已在预算之外，turn 以
        # time_budget 收场，只跑了一个迭代。
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

        rate = type("RateLimitError", (Exception,), {})

        class BurningProvider(MockProvider):
            """Each attempt consumes simulated wall clock before failing."""

            def __init__(self, responses, clock: FakeClock, burn: float):
                super().__init__(responses)
                self._clock = clock
                self._burn = burn

            def is_retriable(self, exc: Exception) -> bool:
                return isinstance(exc, rate)

            async def stream(self, messages, system, tools, max_tokens, effort):
                self.calls.append({"messages": list(messages)})
                advance(self._clock, self._burn)  # 每次尝试先烧掉一段假墙钟
                outcome = self.responses.pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                async for chunk in response_to_chunks(outcome):
                    yield chunk

        clock = FakeClock()
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

    async def test_the_iteration_limit_surfaces_per_convenience_api(self):
        """chat() 把预算花光当异常抛出；run_with_messages() 把它当结果回报。"""
        agent = make_agent(echo_tool(), config=AgentConfig(max_iterations=2))
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}')]

        with pytest.raises(IterationLimit) as exc:
            await agent.chat("go")

        assert exc.value.iterations == 2
        assert agent.state.status == DONE  # a budget cut is an ending, not a failure

        result = await agent.run_with_messages([{"role": "user", "content": "hi"}])

        assert result.had_error is True
        assert "iterations" in result.content


# ── request interception ────────────────────────────────────


class TestRequestInterception:
    async def test_before_request_may_rewrite_the_messages_and_runs_last(self):
        order: list[str] = []

        class Both(AgentHook):
            async def before_iteration(self, ctx: IterationContext) -> None:
                order.append("iteration")

            async def before_request(self, ctx: RequestContext) -> None:
                order.append("request")
                ctx.messages = [
                    {"role": "user", "content": "replaced before the wire"}
                ]

        agent = make_agent(provider=MockProvider([say("done")]), hooks=[Both()])

        await collect(agent.stream("hi"))

        # before_iteration 先跑，before_request 后跑，各就各位。
        assert order == ["iteration", "request"]
        # 唯一一次请求：一条 user 消息，内容是钩子换上的。
        (only,) = agent.provider.calls
        assert [m["role"] for m in only["messages"]] == ["user"]
        assert only["messages"][0]["content"] == "replaced before the wire"

    async def test_the_tools_snapshot_is_what_the_request_carries(self):
        seen: list[dict] = []

        class Watcher(AgentHook):
            async def before_request(self, ctx: RequestContext) -> None:
                seen.append(ctx.tools)
                # An in-place edit reaches this request alone — never the
                # registry, and never a frozen payload.
                ctx.tools.append({"type": "function", "function": {"name": "ghost"}})

        agent = make_agent(echo_tool(), provider=MockProvider([say("done")]), hooks=[Watcher()])

        await collect(agent.stream("hi"))

        # 钩子看到的快照就是唯一那次请求带走的快照。
        (only,) = agent.provider.calls
        assert seen == [only["tools"]]
        sent = [s["function"]["name"] for s in only["tools"]]
        assert sent == ["echo", "ghost"]
        # The registry — including anything frozen — never saw the ghost.
        assert [s["function"]["name"] for s in agent.tool_registry.all_schemas()] == ["echo"]

    async def test_after_response_sees_the_response_and_may_correct_the_usage(self):
        seen: list[tuple[str | None, int]] = []

        class Auditor(AgentHook):
            async def after_response(self, ctx: ResponseContext) -> None:
                seen.append((ctx.finish_reason, ctx.iteration))
                ctx.usage = Usage(10, 20)

        agent = make_agent(echo_tool(), hooks=[Auditor()])
        agent.provider.responses = [
            tool_call_response("echo", '{"value": "x"}'),
            say("done"),
        ]

        events = await collect(agent.stream("hi"))

        # 钩子读得到 finish_reason 与迭代号（两次请求各一次）。
        assert seen == [("tool_calls", 1), ("stop", 2)]
        # ……改写的用量落在迭代事件上，并累计进 turn 的汇总。
        iteration = next(e for e in events if e.type == "iteration_finished")
        assert (iteration.usage.prompt_tokens, iteration.usage.completion_tokens) == (10, 20)
        assert events[-1].usage.prompt_tokens == 20  # the turn's totals add up

    async def test_a_raising_interception_hook_does_not_break_the_turn(self):
        class Bad(AgentHook):
            async def before_request(self, ctx: RequestContext) -> None:
                raise RuntimeError("boom")

            async def after_response(self, ctx: ResponseContext) -> None:
                raise RuntimeError("bam")

        agent = make_agent(provider=MockProvider([say("still fine")]), hooks=[Bad()])

        events = await collect(agent.stream("hi"))

        assert events[-1].content == "still fine"


# ── hook event channel ──────────────────────────────────────


class TestEventChannel:
    async def test_an_emitted_event_reaches_both_hooks_and_the_stream(self):
        """迭代钩子与工具钩子发出的 Notice：钩子侧与事件流都收得到。"""
        hook_notices: list[str] = []

        class Plugin(AgentHook):
            def __init__(self) -> None:
                self.emitted = False

            async def before_iteration(self, ctx: IterationContext) -> None:
                if not self.emitted:
                    self.emitted = True
                    await ctx.emit(Notice(message="hello from a plugin"))

            async def on_event(self, event: Event) -> None:
                if isinstance(event, Notice):
                    hook_notices.append(event.message)

        class Emitter(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                await ctx.emit(Notice(message=f"tool {ctx.tool_name}"))

        agent = make_agent(echo_tool(), hooks=[Plugin(), Emitter()])
        agent.provider.responses = [tool_call_response("echo", '{"value": "x"}'), say("done")]

        events = await collect(agent.stream("hi"))

        notices = [e.message for e in events if isinstance(e, Notice)]
        assert notices == ["hello from a plugin", "tool echo"]
        # on_event 是带内订阅者：流里每条 Notice 它都看见。
        assert hook_notices == notices
        # Emitted from on_tool_start, so it precedes the call it is about.
        assert events.index(
            next(e for e in events if isinstance(e, Notice) and e.message == "tool echo")
        ) < next(
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

    def test_overrides_are_independent(self):
        parent = make_agent(echo_tool("a"), echo_tool("b"))

        replacement = MockProvider([], model="cheap-model")
        child = parent.derive(
            provider=replacement,
            system_prompt="child",
            tools=parent.tool_registry.select(include_names={"a"}),
            config=parent.config.replace(max_iterations=3),
        )

        assert child.provider is replacement
        assert parent.provider is not replacement
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

    async def test_a_provider_failure_surfaces_per_convenience_api(self):
        """chat() 让异常上溯；run_with_messages() 把它装进错误结果。"""
        class Dead(MockProvider):
            async def stream(self, *args):
                raise RuntimeError("upstream is down")
                yield  # pragma: no cover — makes this a generator

        agent = make_agent(provider=Dead())

        with pytest.raises(RuntimeError, match="upstream is down"):
            await agent.chat("hello")

        assert agent.state.status == "failed"

        result = await agent.run_with_messages([{"role": "user", "content": "hi"}])

        assert result.had_error is True
        assert "upstream is down" in result.content


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
        agent = make_agent(provider=MockProvider([say("hello")], chunk_size=1))

        turn = agent.start("hi")
        # Two readers subscribe after the start; each sees the whole run.
        first = turn.subscribe()
        second = turn.subscribe()

        watched = await collect(first)
        mirrored = await collect(second)

        assert isinstance(watched[0], RunStarted)
        assert isinstance(watched[-1], RunFinished)
        assert all(event.run_id == turn.id for event in watched)
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
    async def test_a_running_sync_tool_is_told_to_stand_down(self):
        """计时器到点或取消整轮：worker 都从取消信号里得知自己被放弃。"""
        # 1) 循环的计时器决定超时：A tenth of a second — the worker is
        #    still inside its wait when the timer fires.
        timed_out = threading.Event()
        abandoned_by_timer = threading.Event()
        agent = make_agent(
            _patient_tool(timed_out, abandoned_by_timer),
            config=AgentConfig(tool_timeout=0.1),
        )
        agent.provider = MockProvider(
            [tool_call_response("patient"), say("given up waiting")]
        )

        events = await collect(agent.stream("hi"))

        finished = next(e for e in events if isinstance(e, ToolCallFinished))
        assert finished.status == "timeout"
        await _thread_event(timed_out)
        await _thread_event(abandoned_by_timer)
        assert abandoned_by_timer.is_set()  # the worker noticed it was abandoned

        # 2) 取消整轮：同一条信号送达仍在运行的 worker。
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


def _vetoed_tool() -> tuple[Tool, list[AgentHook]]:
    """A call a hook vetoes — the tool body must never run."""

    def never_runs(args):
        raise AssertionError("a vetoed tool does not execute")

    class Vetoer(AgentHook):
        async def on_tool_start(self, ctx: ToolCallContext) -> None:
            ctx.deny = "not allowed here"

    tool = Tool(
        "risky",
        "d",
        {"type": "object", "properties": {"value": {"type": "string"}}},
        never_runs,
    )
    return tool, [Vetoer()]


class TestErrorPrefixes:
    """Every failed call's persisted result says so with a prefix.

    A saved session is a message list — the prefix is the only place an
    outcome survives for whoever replays it (see core/tool.py). The status
    and the code are asserted in the one place the outcome is read; the
    denied row is also where the veto is proven (the tool never runs).
    """

    @pytest.mark.parametrize(
        "name,factory,tool_timeout,status,error_code,prefix",
        [
            ("boom", lambda: (_boom_tool(), []), 5, "error", "custom_code", f"{ERROR_PREFIX} custom_code:"),
            # 钩子否决的调用：denied 前缀，工具根本不跑。
            ("risky", _vetoed_tool, 5, "denied", None, DENIED_PREFIX),
            # 没注册的工具：调用发出去了，注册表答不上来。
            ("ghost", lambda: (None, []), 5, "not_found", None, f"{ERROR_PREFIX} unknown tool"),
            ("patient", lambda: (_parking_tool(), []), 0.05, "timeout", None, TIMEOUT_PREFIX),
        ],
    )
    async def test_the_persisted_result_says_what_happened(
        self, name, factory, tool_timeout, status, error_code, prefix
    ):
        tool, hooks = factory()
        agent = (
            make_agent(tool, hooks=hooks, config=AgentConfig(tool_timeout=tool_timeout))
            if tool
            else make_agent(config=AgentConfig(tool_timeout=tool_timeout))
        )
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
