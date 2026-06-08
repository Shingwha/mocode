"""Tests for subagent — SubAgent, SubAgentConfig, SubAgentTool."""

import pytest

from mocode.core import (
    Response,
    ToolCall,
    Tool,
    ToolRegistry,
    SubAgent,
    SubAgentConfig,
)
from mocode.tools.subagent import SubAgentTool
from mocode.core.tool import ToolError


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


class MockAgent:
    """Minimal agent-like object with a .provider attribute."""

    def __init__(self, provider):
        self.provider = provider


# ---- SubAgent ----


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_run_returns_content(self):
        provider = MockProvider(responses=[Response(content="Hello from sub")])
        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(agent=MockAgent(provider), tools=ToolRegistry(), config=cfg)
        result = await sub.run("do something")
        assert result.content == "Hello from sub"
        assert result.had_error is False

    @pytest.mark.asyncio
    async def test_run_with_tool_calls(self):
        tool = Tool(
            "echo",
            "Echo",
            {"text": {"type": "string", "description": "text"}},
            lambda a: f"echoed: {a['text']}",
        )
        registry = ToolRegistry()
        registry.register(tool)

        provider = MockProvider(
            responses=[
                Response(
                    content="",
                    tool_calls=[
                        ToolCall(id="1", name="echo", arguments='{"text": "hello"}')
                    ],
                ),
                Response(content="Got echo result"),
            ]
        )
        cfg = SubAgentConfig(system_prompt="test")
        sub = SubAgent(agent=MockAgent(provider), tools=registry, config=cfg)
        result = await sub.run("run echo")

        assert result.content == "Got echo result"
        assert result.tool_calls_made == 1
        assert len(result.messages) == 4

    @pytest.mark.asyncio
    async def test_max_tool_calls_limit(self):
        tool = Tool("noop", "Noop", {}, lambda a: "ok")
        registry = ToolRegistry()
        registry.register(tool)

        always_call = Response(
            content="", tool_calls=[ToolCall(id="1", name="noop", arguments="{}")]
        )
        provider = MockProvider(responses=[always_call] * 10)
        cfg = SubAgentConfig(system_prompt="test", max_tool_calls=3)
        sub = SubAgent(agent=MockAgent(provider), tools=registry, config=cfg)
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
        sub = SubAgent(
            agent=MockAgent(FailingProvider()), tools=ToolRegistry(), config=cfg
        )
        result = await sub.run("test")
        assert result.had_error is True


# ---- SubAgentTool ----


class TestSubAgentTool:
    @pytest.mark.asyncio
    async def test_returns_content(self):
        tool = Tool(
            "echo",
            "Echo",
            {"text": {"type": "string", "description": "text"}},
            lambda a: a["text"],
        )
        registry = ToolRegistry()
        registry.register(tool)

        provider = MockProvider(
            responses=[
                Response(
                    content="",
                    tool_calls=[
                        ToolCall(id="1", name="echo", arguments='{"text": "hi"}')
                    ],
                ),
                Response(content="result from sub"),
            ]
        )

        sub_tool = SubAgentTool(MockAgent(provider), registry, system_prompt="parent prompt")
        result = await sub_tool.run_async({"task": "say hi"})
        assert result == "result from sub"

    @pytest.mark.asyncio
    async def test_missing_task(self):
        registry = ToolRegistry()
        sub_tool = SubAgentTool(MockAgent(MockProvider()), registry, system_prompt="parent prompt")
        with pytest.raises(ToolError, match="task"):
            await sub_tool.run_async({})

    @pytest.mark.asyncio
    async def test_subagent_inherits_parent_prompt(self):
        """Verify that SubAgent's system prompt includes the parent prompt content."""
        captured_system = {}

        class SpyProvider:
            def __init__(self):
                self._model = "test"

            @property
            def model(self):
                return self._model

            async def call(self, messages, system, tools, max_tokens):
                captured_system["value"] = system
                return Response(content="done")

        provider = SpyProvider()
        parent_prompt = "PARENT_AGENT_INSTRUCTIONS"
        sub_tool = SubAgentTool(MockAgent(provider), ToolRegistry(), system_prompt=parent_prompt)
        await sub_tool.run_async({"task": "test"})

        sys = captured_system["value"]
        # Parent prompt content is preserved
        assert parent_prompt in sys
        # Sub-agent identity is prepended
        assert "sub-agent" in sys
        # Sub-agent guidelines are appended
        assert "Work autonomously" in sys
