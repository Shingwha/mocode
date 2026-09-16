"""Tests for the agent loop's extension primitives: derive, events, interception."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from mocode.core.agent import AgentConfig, AgentLoop
from mocode.core.hook import AgentHook, HookRunner, IterationContext, ToolCallContext
from mocode.core.provider import Response, ToolCall, Usage
from mocode.core.tool import Tool, ToolError, ToolRegistry


class MockProvider:
    """Returns canned responses in order and records every call."""

    def __init__(self, responses: list[Response] | None = None):
        self.responses = list(responses or [Response(content="done", usage=Usage(1, 1))])
        self.calls: list[dict] = []

    @property
    def model(self) -> str:
        return "mock"

    def is_retriable(self, exc: Exception) -> bool:
        return False

    async def call(self, messages, system, tools, max_tokens) -> Response:
        self.calls.append(
            {"messages": list(messages), "system": system, "tools": tools, "max_tokens": max_tokens}
        )
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def _tool_call_response(name: str, args: str = "{}") -> Response:
    return Response(
        content=None,
        tool_calls=[ToolCall(id="c1", name=name, arguments=args)],
        usage=Usage(1, 1),
        finish_reason="tool_calls",
    )


def _make_agent(*tools: Tool, hooks: list[AgentHook] | None = None, config=None) -> AgentLoop:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return AgentLoop(
        provider=MockProvider(),
        system_prompt="sys",
        tools=registry,
        hooks=HookRunner(hooks or []),
        config=config or AgentConfig(),
    )


def _echo_tool(name: str = "echo", **kwargs) -> Tool:
    return Tool(
        name=name,
        description="echo",
        params={"value": {"type": "string", "description": "v"}},
        func=lambda args: f"echo:{args['value']}",
        **kwargs,
    )


# ── derive ──────────────────────────────────────────────────


class TestDerive:
    def test_shares_provider_and_starts_empty(self):
        parent = _make_agent(_echo_tool())
        parent.messages.append({"role": "user", "content": "old"})

        child = parent.derive()

        assert child.provider is parent.provider
        assert child.system_prompt == parent.system_prompt
        assert child.config == parent.config
        assert child.messages == []
        assert child.tool_registry is parent.tool_registry

    def test_overrides_are_independent(self):
        parent = _make_agent(_echo_tool("a"), _echo_tool("b"))

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

    def test_child_hooks_are_isolated(self):
        parent = _make_agent(_echo_tool())
        child = parent.derive(hooks=HookRunner([AgentHook()]))
        assert child.hooks is not parent.hooks

    @pytest.mark.asyncio
    async def test_child_runs_independently(self):
        parent = _make_agent(_echo_tool())
        parent.provider.responses = [Response(content="child answer", usage=Usage(1, 1))]

        result = await parent.derive().run_with_messages(
            [{"role": "user", "content": "hi"}]
        )

        assert result.content == "child answer"
        assert result.had_error is False
        assert parent.messages == []


# ── events ──────────────────────────────────────────────────


@dataclass
class Notice:
    text: str


class _EmittingHook(AgentHook):
    async def before_iteration(self, ctx: IterationContext) -> None:
        await ctx.emit(Notice("from before_iteration"))


class _RecordingHook(AgentHook):
    def __init__(self):
        self.seen: list[object] = []

    async def on_event(self, event: object) -> None:
        self.seen.append(event)


class TestEventChannel:
    @pytest.mark.asyncio
    async def test_emit_reaches_every_hook(self):
        listener = _RecordingHook()
        agent = _make_agent(_echo_tool(), hooks=[_EmittingHook(), listener])

        await agent.chat("hi")

        assert [type(e).__name__ for e in listener.seen] == ["Notice"]
        assert listener.seen[0].text == "from before_iteration"

    @pytest.mark.asyncio
    async def test_emit_from_tool_context(self):
        class ToolEmitter(AgentHook):
            def __init__(self):
                self.seen = []

            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                await ctx.emit(Notice(f"tool {ctx.tool_name}"))

            async def on_event(self, event: object) -> None:
                self.seen.append(event)

        hook = ToolEmitter()
        agent = _make_agent(_echo_tool())
        agent.provider.responses = [
            _tool_call_response("echo", '{"value": "x"}'),
            Response(content="done", usage=Usage(1, 1)),
        ]
        agent.hooks = HookRunner([hook])

        await agent.chat("hi")

        assert [e.text for e in hook.seen] == ["tool echo"]

    @pytest.mark.asyncio
    async def test_iteration_counter_is_reported(self):
        seen: list[int] = []

        class Counter(AgentHook):
            async def before_iteration(self, ctx: IterationContext) -> None:
                seen.append(ctx.iteration)

        agent = _make_agent(_echo_tool())
        agent.provider.responses = [
            _tool_call_response("echo", '{"value": "x"}'),
            Response(content="done", usage=Usage(1, 1)),
        ]
        agent.hooks = HookRunner([Counter()])

        await agent.chat("hi")

        assert seen == [1, 2]
        assert agent.iteration == 2


# ── tool interception ───────────────────────────────────────


class TestToolInterception:
    @pytest.mark.asyncio
    async def test_deny_vetoes_execution(self):
        executed = []

        def _run(args):
            executed.append(args)
            return "should not run"

        class Denier(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                ctx.deny = "not allowed here"

        tool = Tool("risky", "d", {"value": {"type": "string", "description": "v"}}, _run)
        agent = _make_agent(tool, hooks=[Denier()])
        agent.provider.responses = [
            _tool_call_response("risky", '{"value": "x"}'),
            Response(content="done", usage=Usage(1, 1)),
        ]

        await agent.chat("hi")

        assert executed == []
        tool_msg = next(m for m in agent.messages if m["role"] == "tool")
        assert tool_msg["content"].startswith("denied:")

    @pytest.mark.asyncio
    async def test_args_can_be_rewritten(self):
        class Rewriter(AgentHook):
            async def on_tool_start(self, ctx: ToolCallContext) -> None:
                ctx.tool_args["value"] = "rewritten"

        agent = _make_agent(_echo_tool(), hooks=[Rewriter()])
        agent.provider.responses = [
            _tool_call_response("echo", '{"value": "original"}'),
            Response(content="done", usage=Usage(1, 1)),
        ]

        await agent.chat("hi")

        tool_msg = next(m for m in agent.messages if m["role"] == "tool")
        assert tool_msg["content"] == "echo:rewritten"

    @pytest.mark.asyncio
    async def test_result_can_be_rewritten(self):
        class Redactor(AgentHook):
            async def on_tool_complete(self, ctx: ToolCallContext) -> None:
                ctx.tool_result = "[redacted]"

        agent = _make_agent(_echo_tool(), hooks=[Redactor()])
        agent.provider.responses = [
            _tool_call_response("echo", '{"value": "secret"}'),
            Response(content="done", usage=Usage(1, 1)),
        ]

        await agent.chat("hi")

        tool_msg = next(m for m in agent.messages if m["role"] == "tool")
        assert tool_msg["content"] == "[redacted]"


class TestToolStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool,expected",
        [
            (Tool("boom", "d", {}, lambda a: (_ for _ in ()).throw(RuntimeError("kaboom"))), "error"),
            (Tool("bad", "d", {}, lambda a: (_ for _ in ()).throw(ToolError("nope", "teapot"))), "error"),
            (Tool("slow", "d", {}, lambda a: __import__("time").sleep(1)), "timeout"),
        ],
    )
    async def test_status_values(self, tool, expected):
        seen: list[tuple[str, str | None]] = []

        class Recorder(AgentHook):
            async def on_tool_complete(self, ctx: ToolCallContext) -> None:
                seen.append((ctx.status, ctx.error_code))

        agent = _make_agent(tool, hooks=[Recorder()], config=AgentConfig(tool_timeout=0.05 if tool.name == "slow" else 5))
        agent.provider.responses = [
            _tool_call_response(tool.name),
            Response(content="done", usage=Usage(1, 1)),
        ]

        await agent.chat("hi")

        assert seen[0][0] == expected

    @pytest.mark.asyncio
    async def test_unknown_tool_reports_not_found(self):
        seen: list[str] = []

        class Recorder(AgentHook):
            async def on_tool_complete(self, ctx: ToolCallContext) -> None:
                seen.append(ctx.status)

        agent = _make_agent(_echo_tool(), hooks=[Recorder()])
        agent.provider.responses = [
            _tool_call_response("ghost"),
            Response(content="done", usage=Usage(1, 1)),
        ]

        await agent.chat("hi")

        assert seen == ["not_found"]
        assert "unknown tool" in next(m for m in agent.messages if m["role"] == "tool")["content"]

    @pytest.mark.asyncio
    async def test_ok_status_and_error_code(self):
        recorded: list[tuple[str, str | None]] = []

        class Recorder(AgentHook):
            async def on_tool_complete(self, ctx: ToolCallContext) -> None:
                recorded.append((ctx.status, ctx.error_code))

        failing = Tool(
            "teapot",
            "d",
            {},
            lambda a: (_ for _ in ()).throw(ToolError("I am a teapot", "teapot_code")),
        )
        agent = _make_agent(_echo_tool(), failing, hooks=[Recorder()])
        agent.provider.responses = [
            Response(
                content=None,
                tool_calls=[
                    ToolCall(id="c1", name="echo", arguments='{"value": "a"}'),
                    ToolCall(id="c2", name="teapot", arguments="{}"),
                ],
                usage=Usage(1, 1),
                finish_reason="tool_calls",
            ),
            Response(content="done", usage=Usage(1, 1)),
        ]

        await agent.chat("hi")

        assert sorted(recorded) == [("error", "teapot_code"), ("ok", None)]


class TestMalformedToolArgs:
    @pytest.mark.asyncio
    async def test_invalid_json_becomes_error_result(self):
        agent = _make_agent(_echo_tool())
        agent.provider.responses = [
            _tool_call_response("echo", "{not json"),
            Response(content="done", usage=Usage(1, 1)),
        ]

        await agent.chat("hi")

        tool_msg = next(m for m in agent.messages if m["role"] == "tool")
        assert tool_msg["content"].startswith("error:")
        assert "invalid JSON" in tool_msg["content"]


# ── tool metadata ───────────────────────────────────────────


class TestToolMetadata:
    def test_summary_key_defaults_to_first_param(self):
        tool = Tool("t", "d", {"pattern": {"type": "string", "description": "p"}}, lambda a: "")
        assert tool.summary_key == "pattern"

    def test_summary_key_can_be_declared(self):
        tool = _echo_tool(summary_key="value")
        assert tool.summary_key == "value"

    def test_tags_are_frozen(self):
        tool = _echo_tool(tags={"fs", "demo"})
        assert tool.tags == frozenset({"fs", "demo"})

    def test_select_filters(self):
        registry = ToolRegistry()
        registry.register(_echo_tool("a", tags={"fs"}))
        registry.register(_echo_tool("b", tags={"shell"}))
        registry.register(_echo_tool("c"))

        assert registry.select(include_tags={"fs"}).names() == ["a"]
        assert registry.select(exclude_tags={"fs"}).names() == ["b", "c"]
        assert registry.select(exclude_names={"c"}).names() == ["a", "b"]
        assert registry.select(include_names={"b", "c"}).names() == ["b", "c"]
        assert len(registry.select()) == 3

    def test_select_returns_a_view_sharing_instances(self):
        registry = ToolRegistry()
        tool = _echo_tool("a")
        registry.register(tool)
        assert registry.select().get("a") is tool
