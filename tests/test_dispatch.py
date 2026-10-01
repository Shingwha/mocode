"""ToolDispatcher — the one execution path, for the loop and for programs."""

from __future__ import annotations

import time

import pytest

from mocode.core.agent import AgentConfig, AgentLoop
from mocode.core.dispatch import DispatchResult, ToolDispatcher
from mocode.core.events import Event, ToolCallFinished, ToolCallStarted
from mocode.core.hook import AgentHook, HookRunner, ToolCallContext
from mocode.core.provider import Response, Usage
from mocode.core.tool import Tool, ToolError, ToolRegistry, ToolResult

from .providers import MockProvider, tool_call_response


def _echo_tool(name: str = "echo", **kwargs) -> Tool:
    return Tool(
        name=name,
        description="echo",
        params={"value": {"type": "string", "description": "v"}},
        func=lambda args: f"echo:{args['value']}",
        **kwargs,
    )


def _failing(exc: Exception) -> Tool:
    def run(args):
        raise exc

    return Tool("boom", "d", {}, run)


def _sleeper() -> Tool:
    return Tool("slow", "d", {}, lambda a: time.sleep(1))


def _plain_answer(text: str = "done") -> Response:
    return Response(content=text, usage=Usage(1, 1), finish_reason="stop")


def _make_agent(
    *tools: Tool,
    hooks: list[AgentHook] | None = None,
    config: AgentConfig | None = None,
    provider: MockProvider | None = None,
) -> AgentLoop:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return AgentLoop(
        provider=provider or MockProvider(),
        system_prompt="sys",
        tools=registry,
        hooks=HookRunner(hooks or []),
        config=config or AgentConfig(),
    )


def _sink(events: list[Event], folds: list[bool]):
    """A publish callback that records what the dispatcher sent — bare core."""

    async def publish(event: Event, *, fold: bool) -> None:
        events.append(event)
        folds.append(fold)

    return publish


def _bare_dispatcher(
    *tools: Tool,
    hooks: list[AgentHook] | None = None,
    config: AgentConfig | None = None,
) -> tuple[ToolDispatcher, list[Event], list[bool]]:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    events: list[Event] = []
    folds: list[bool] = []
    dispatcher = ToolDispatcher(
        registry, HookRunner(hooks or []), config or AgentConfig(), _sink(events, folds)
    )
    return dispatcher, events, folds


class _Denier(AgentHook):
    def __init__(self, reason: str = "not allowed") -> None:
        self.reason = reason

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        ctx.deny = self.reason


# ── bare core: the dispatcher without a loop ─────────────────


class TestBareCoreDispatcher:
    @pytest.mark.asyncio
    async def test_a_call_runs_the_tool_and_publishes_its_events(self):
        dispatcher, events, _ = _bare_dispatcher(_echo_tool())

        result = await dispatcher.run("echo", {"value": "x"}, call_id="c1")

        assert isinstance(result, DispatchResult)
        assert (result.status, result.content, result.call_id) == ("ok", "echo:x", "c1")
        assert [type(e) for e in events] == [ToolCallStarted, ToolCallFinished]
        assert events[0].args == {"value": "x"}
        assert events[1].status == "ok"

    @pytest.mark.asyncio
    async def test_model_origin_events_fold_program_origin_events_do_not(self):
        tool = _echo_tool()

        model, _, model_folds = _bare_dispatcher(tool)
        await model.run("echo", {"value": "x"})
        assert model_folds == [True, True]

        program, _, program_folds = _bare_dispatcher(tool)
        await program.run("echo", {"value": "x"}, origin="program")
        assert program_folds == [False, False]

    @pytest.mark.asyncio
    async def test_structured_details_travel_on_the_result(self):
        tool = Tool("stats", "d", {}, lambda a: ToolResult("read it", {"lines": 412}))

        dispatcher, events, _ = _bare_dispatcher(tool)
        result = await dispatcher.run("stats", {})

        assert result.details == {"lines": 412}
        assert events[1].details == {"lines": 412}
        assert result.content == "read it"

    @pytest.mark.asyncio
    async def test_results_are_truncated_to_the_configured_limit(self):
        tool = Tool("big", "d", {}, lambda a: "x" * 100)

        dispatcher, _, _ = _bare_dispatcher(
            tool, config=AgentConfig(tool_result_limit=10)
        )
        result = await dispatcher.run("big", {})

        assert result.content == "x" * 10 + "\n... [truncated]"

    @pytest.mark.asyncio
    async def test_a_per_call_timeout_overrides_the_config(self):
        dispatcher, _, _ = _bare_dispatcher(_sleeper(), config=AgentConfig(tool_timeout=30))
        result = await dispatcher.run("slow", {}, timeout=0.05)

        assert result.status == "timeout"
        assert result.content.startswith("timeout:")


# ── call identity ────────────────────────────────────────────


class TestCallIdentity:
    @pytest.mark.asyncio
    async def test_program_calls_nested_in_a_parent_are_numbered_per_parent(self):
        dispatcher, events, _ = _bare_dispatcher(_echo_tool())

        await dispatcher.run("echo", {}, origin="program", parent_call_id="p9")
        await dispatcher.run("echo", {}, origin="program", parent_call_id="p9")
        await dispatcher.run("echo", {}, origin="program", parent_call_id="other")

        ids = [e.call_id for e in events if isinstance(e, ToolCallStarted)]
        assert ids == ["p9:1", "p9:2", "other:1"]

    @pytest.mark.asyncio
    async def test_a_parentless_program_call_gets_a_pcall_id(self):
        dispatcher, events, _ = _bare_dispatcher(_echo_tool())

        await dispatcher.run("echo", {}, origin="program")
        await dispatcher.run("echo", {}, origin="program")

        ids = [e.call_id for e in events if isinstance(e, ToolCallStarted)]
        assert ids == ["pcall_1", "pcall_2"]

    @pytest.mark.asyncio
    async def test_model_calls_keep_the_provider_id_or_get_one_made_up(self):
        dispatcher, events, _ = _bare_dispatcher(_echo_tool())

        await dispatcher.run("echo", {}, call_id="from-provider")
        await dispatcher.run("echo", {})

        ids = [e.call_id for e in events if isinstance(e, ToolCallStarted)]
        assert ids == ["from-provider", "call_2"]


# ── outcome parity: the origins cannot drift ─────────────────


class TestOutcomeParity:
    """Deny, timeout and every error mean the same thing whatever the origin."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,tool,kwargs,expected",
        [
            (
                "boom",
                _failing(RuntimeError("kaboom")),
                {},
                ("error", "error: kaboom", None),
            ),
            (
                "boom",
                _failing(ToolError("nope", "teapot")),
                {},
                ("error", "error: teapot: nope", "teapot"),
            ),
            (
                "echo",
                _echo_tool(),
                {"parse_error": "error: invalid JSON arguments (boom)"},
                ("error", "error: invalid JSON arguments (boom)", None),
            ),
            (
                "ghost",
                _echo_tool(),
                {},
                ("not_found", "error: unknown tool 'ghost'", None),
            ),
        ],
    )
    async def test_outcomes_are_identical_across_origins(
        self, name, tool, kwargs, expected
    ):
        outcomes = {}
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(tool)
            result = await dispatcher.run(name, {}, origin=origin, **kwargs)
            outcomes[origin] = (result.status, result.content, result.error_code)
        assert outcomes["model"] == outcomes["program"] == expected

    @pytest.mark.asyncio
    async def test_a_vetoed_call_is_denied_for_both_origins(self):
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(_echo_tool(), hooks=[_Denier("no")])
            result = await dispatcher.run("echo", {"value": "x"}, origin=origin)
            assert (result.status, result.content) == ("denied", "denied: no")

    @pytest.mark.asyncio
    async def test_a_timeout_is_a_timeout_whatever_the_origin(self):
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(
                _sleeper(), config=AgentConfig(tool_timeout=0.05)
            )
            result = await dispatcher.run("slow", {}, origin=origin)
            assert result.status == "timeout"
            assert result.content.startswith("timeout:")

    @pytest.mark.asyncio
    async def test_a_switched_off_tool_refuses_for_both_origins(self):
        for origin in ("model", "program"):
            dispatcher, _, _ = _bare_dispatcher(_echo_tool())
            dispatcher.registry.disable("echo")
            result = await dispatcher.run("echo", {"value": "x"}, origin=origin)
            assert result.status == "denied"
            assert "switched off" in result.content


# ── program origin inside a real loop ────────────────────────


class TestProgramOriginInsideALoop:
    @pytest.mark.asyncio
    async def test_a_nested_call_is_observable_but_not_conversation(self):
        async def bridge(args, ctx):
            first = await agent.dispatcher.run(
                "echo",
                {"value": "one"},
                origin="program",
                parent_call_id=ctx.tool_call_id,
            )
            second = await agent.dispatcher.run(
                "echo",
                {"value": "two"},
                origin="program",
                parent_call_id=ctx.tool_call_id,
            )
            return f"{first.content}+{second.content}"

        agent = _make_agent(_echo_tool(), Tool("bridge", "b", {}, bridge))
        agent.provider = MockProvider([tool_call_response("bridge"), _plain_answer()])

        events = [event async for event in agent.stream("hi")]

        # The nested calls are visible to the turn's readers, numbered per parent.
        started = {e.call_id: e.name for e in events if isinstance(e, ToolCallStarted)}
        assert started == {"c1": "bridge", "c1:1": "echo", "c1:2": "echo"}
        # They belong to the same run — that is why the turn's view carries them.
        assert len({e.run_id for e in events}) == 1

        # The conversation stays the model's story: one tool message, and it is
        # the bridge's own result, not the nested echoes.
        tool_messages = [m for m in agent.messages if m["role"] == "tool"]
        assert [(m["tool_call_id"], m["content"]) for m in tool_messages] == [
            ("c1", "echo:one+echo:two")
        ]
        # The live state and the run's summary count model calls only.
        assert set(agent.state.tool_calls) == {"c1"}
        assert agent.state.tool_calls_made == 1
        assert events[-1].tool_calls_made == 1
