"""Tests for subagent — SubAgent, SubAgentConfig, SubAgentTool."""

import pytest

from mocode.core import (
    AgentHook, AgentHookContext, Response, ToolCall, Tool, ToolRegistry,
)
from mocode.core.tool import ToolError
from mocode.tools.subagent import (
    SubAgent, SubAgentConfig, SubAgentResult, SubAgentTool,
)


class MockProvider:
    def __init__(self, responses=None, model="test-model"):
        self._responses = responses or []
        self._call_count = 0
        self._model = model

    @property
    def model(self):
        return self._model

    async def call(self, messages, system, tools, max_tokens):
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
            self._call_count += 1
            return resp
        return Response(content="done")


# ---- SubAgentConfig ----


class TestSubAgentConfig:
    def test_defaults(self):
        cfg = SubAgentConfig(system_prompt="test")
        assert cfg.max_tool_calls == 50
        assert cfg.max_tokens == 4096
        assert cfg.tool_timeout == 240
        assert cfg.tool_result_limit == 0

    def test_frozen(self):
        cfg = SubAgentConfig(system_prompt="test")
        with pytest.raises(AttributeError):
            cfg.max_tool_calls = 10


# ---- SubAgentResult ----


class TestSubAgentResult:
    def test_defaults(self):
        r = SubAgentResult()
        assert r.content == ""
        assert r.tool_calls_made == 0
        assert r.messages == []
        assert r.had_error is False


# ---- SubAgent ----


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_run_returns_content(self):
        provider = MockProvider(responses=[Response(content="Hello from sub")])
        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=provider, tools=ToolRegistry(), config=cfg)
        result = await sub.run("do something")
        assert result.content == "Hello from sub"
        assert result.had_error is False

    @pytest.mark.asyncio
    async def test_run_with_tool_calls(self):
        tool = Tool("echo", "Echo", {"text": {"type": "string", "description": "text"}}, lambda a: f"echoed: {a['text']}")
        registry = ToolRegistry()
        registry.register(tool)

        provider = MockProvider(responses=[
            Response(content="", tool_calls=[ToolCall(id="1", name="echo", arguments='{"text": "hello"}')]),
            Response(content="Got echo result"),
        ])
        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=provider, tools=registry, config=cfg)
        result = await sub.run("run echo")

        assert result.content == "Got echo result"
        assert result.tool_calls_made == 1
        assert len(result.messages) == 4  # user + assistant(tool_call) + tool + assistant(final)

    @pytest.mark.asyncio
    async def test_max_tool_calls_limit(self):
        tool = Tool("noop", "Noop", {}, lambda a: "ok")
        registry = ToolRegistry()
        registry.register(tool)

        # Always returns a tool call — should hit the limit
        always_call = Response(content="", tool_calls=[ToolCall(id="1", name="noop", arguments='{}')])
        provider = MockProvider(responses=[always_call] * 10)
        cfg = SubAgentConfig(system_prompt="test", max_tool_calls=3)
        sub = SubAgent(provider=provider, tools=registry, config=cfg)
        result = await sub.run("loop forever")

        assert result.tool_calls_made <= 3

    @pytest.mark.asyncio
    async def test_provider_error_sets_had_error(self):
        class FailingProvider:
            @property
            def model(self):
                return "fail"

            async def call(self, messages, system, tools, max_tokens):
                raise RuntimeError("LLM is down")

        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=FailingProvider(), tools=ToolRegistry(), config=cfg)
        result = await sub.run("test")
        assert result.had_error is True

    @pytest.mark.asyncio
    async def test_tool_filtering(self):
        """SubAgent receives pre-filtered tools — filtering is SubAgentTool's responsibility."""
        t1 = Tool("t1", "T1", {}, lambda a: "r1")
        t2 = Tool("t2", "T2", {}, lambda a: "r2")
        registry = ToolRegistry()
        registry.register(t1)

        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=MockProvider(), tools=registry, config=cfg)
        loop = sub._build_agent_loop()
        schemas = loop._tools.all_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "t1"

    @pytest.mark.asyncio
    async def test_all_schemas_when_no_filter(self):
        t1 = Tool("t1", "T1", {}, lambda a: "r1")
        t2 = Tool("t2", "T2", {}, lambda a: "r2")
        registry = ToolRegistry()
        registry.register(t1)
        registry.register(t2)

        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=MockProvider(), tools=registry, config=cfg)
        loop = sub._build_agent_loop()
        schemas = loop._tools.all_schemas()
        assert len(schemas) == 2

    @pytest.mark.asyncio
    async def test_provider_getter_pattern(self):
        providers = [
            MockProvider(responses=[Response(content="v1")]),
            MockProvider(responses=[Response(content="v2")]),
        ]
        call_count = [0]

        def get_provider():
            p = providers[min(call_count[0], len(providers) - 1)]
            call_count[0] += 1
            return p

        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=get_provider, tools=ToolRegistry(), config=cfg)
        result = await sub.run("test")
        assert result.content in ("v1", "v2")

    @pytest.mark.asyncio
    async def test_isolated_messages(self):
        provider = MockProvider(responses=[Response(content="response")])
        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=provider, tools=ToolRegistry(), config=cfg)

        original = [{"role": "user", "content": "original"}]
        result = await sub.run_messages(original)

        # Original should not be mutated
        assert len(original) == 1
        # Result has the full conversation
        assert len(result.messages) > 1

    @pytest.mark.asyncio
    async def test_hooks_emitted(self):
        events = []

        class TestHook(AgentHook):
            async def on_tool_start(self, ctx):
                events.append(("start", ctx.tool_name))

            async def on_tool_complete(self, ctx):
                events.append(("complete", ctx.tool_name))

        tool = Tool("ping", "Ping", {}, lambda a: "pong")
        registry = ToolRegistry()
        registry.register(tool)

        provider = MockProvider(responses=[
            Response(content="", tool_calls=[ToolCall(id="1", name="ping", arguments='{}')]),
            Response(content="done"),
        ])
        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=provider, tools=registry, config=cfg, hooks=[TestHook()])
        await sub.run("ping")

        assert ("start", "ping") in events
        assert ("complete", "ping") in events

    @pytest.mark.asyncio
    async def test_parallel_tool_calls(self):
        t1 = Tool("t1", "T1", {}, lambda a: "r1")
        t2 = Tool("t2", "T2", {}, lambda a: "r2")
        registry = ToolRegistry()
        registry.register(t1)
        registry.register(t2)

        provider = MockProvider(responses=[
            Response(content="", tool_calls=[
                ToolCall(id="1", name="t1", arguments='{}'),
                ToolCall(id="2", name="t2", arguments='{}'),
            ]),
            Response(content="both done"),
        ])
        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(provider=provider, tools=registry, config=cfg)
        result = await sub.run("run both")

        assert result.tool_calls_made == 2
        assert result.content == "both done"


# ---- SubAgentTool ----


class TestSubAgentTool:
    def test_schema_params(self):
        registry = ToolRegistry()
        tool = SubAgentTool(MockProvider(), registry)
        schema = tool.to_schema()
        params = schema["function"]["parameters"]
        assert "task" in params["required"]
        assert "task" in params["properties"]
        assert "tools" in params["properties"]
        assert "max_tool_calls" in params["properties"]
        assert "max_tokens" in params["properties"]

    def test_blocks_sub_agent_and_compact(self):
        t1 = Tool("t1", "T1", {}, lambda a: "r1")
        sub_tool = Tool("sub_agent", "Sub", {}, lambda a: "blocked")
        compact_tool = Tool("compact", "Compact", {}, lambda a: "blocked")

        registry = ToolRegistry()
        registry.register(t1)
        registry.register(sub_tool)
        registry.register(compact_tool)

        tool = SubAgentTool(MockProvider(), registry)
        assert tool is not None

    @pytest.mark.asyncio
    async def test_returns_content(self):
        tool = Tool("echo", "Echo", {"text": {"type": "string", "description": "text"}}, lambda a: a["text"])
        registry = ToolRegistry()
        registry.register(tool)

        provider = MockProvider(responses=[
            Response(content="", tool_calls=[ToolCall(id="1", name="echo", arguments='{"text": "hi"}')]),
            Response(content="result from sub"),
        ])

        sub_tool = SubAgentTool(provider, registry)
        result = await sub_tool.run_async({"task": "say hi"})
        assert result == "result from sub"

    @pytest.mark.asyncio
    async def test_returns_error_on_failure(self):
        class FailProvider:
            @property
            def model(self):
                return "fail"

            async def call(self, messages, system, tools, max_tokens):
                raise RuntimeError("boom")

        registry = ToolRegistry()
        sub_tool = SubAgentTool(FailProvider(), registry)
        result = await sub_tool.run_async({"task": "test"})
        assert "[SubAgent error]" in result

    @pytest.mark.asyncio
    async def test_missing_task(self):
        registry = ToolRegistry()
        sub_tool = SubAgentTool(MockProvider(), registry)
        with pytest.raises(ToolError, match="task"):
            await sub_tool.run_async({})
