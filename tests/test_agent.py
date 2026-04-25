"""Agent tests with mocked LLM provider — v0.2 (Response DTO)"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from mocode.agent import Agent
from mocode.config import Config
from mocode.event import EventType, EventBus
from mocode.interrupt import CancellationToken
from mocode.permission import (
    CheckOutcome,
    CheckResult,
    DenyAllPermissionHandler,
    PermissionChecker,
)
from mocode.provider import Response, ToolCall, Usage
from mocode.tool import Tool, ToolRegistry


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.model = "test-model"
    return provider


@pytest.fixture
def agent(mock_provider, registry, config, event_bus, cancel_token):
    return Agent(
        provider=mock_provider,
        system_prompt="You are a test assistant.",
        tools=registry,
        event_bus=event_bus,
        cancel_token=cancel_token,
        permission_checker=None,
        config=config,
    )


class TestSimpleChat:
    @pytest.mark.asyncio
    async def test_simple_chat(self, agent, mock_provider):
        mock_provider.call.return_value = Response(content="Hi there!")
        result = await agent.chat("Hello")
        assert result == "Hi there!"
        assert len(agent.messages) == 2

    @pytest.mark.asyncio
    async def test_text_complete_event(self, agent, mock_provider, event_bus):
        events = []
        event_bus.on(EventType.TEXT_COMPLETE, lambda e: events.append(e.data))
        mock_provider.call.return_value = Response(content="Response text")
        await agent.chat("Hello")
        assert len(events) == 1
        assert events[0]["content"] == "Response text"

    @pytest.mark.asyncio
    async def test_message_added_event(self, agent, mock_provider, event_bus):
        events = []
        event_bus.on(EventType.MESSAGE_ADDED, lambda e: events.append(e.data))
        mock_provider.call.return_value = Response(content="ok")
        await agent.chat("Hello")
        assert events[0]["role"] == "user"


class TestToolCalls:
    @pytest.mark.asyncio
    async def test_tool_call_and_result(self, agent, mock_provider, registry):
        registry.register(Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"]))

        tc = ToolCall(id="tc_1", name="echo", arguments=json.dumps({"msg": "hello"}))
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="Done!"),
        ]

        result = await agent.chat("Do something")
        assert result == "Done!"

    @pytest.mark.asyncio
    async def test_multi_tool_calls(self, agent, mock_provider, registry):
        registry.register(Tool("add", "Add", {"x": "string"}, lambda a: "result_a"))
        registry.register(Tool("sub", "Sub", {"x": "string"}, lambda a: "result_b"))

        tc1 = ToolCall(id="tc_1", name="add", arguments=json.dumps({"x": "1"}))
        tc2 = ToolCall(id="tc_2", name="sub", arguments=json.dumps({"x": "2"}))
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc1, tc2]),
            Response(content="All done"),
        ]

        result = await agent.chat("Multi tool")
        assert result == "All done"

    @pytest.mark.asyncio
    async def test_tool_events(self, agent, mock_provider, registry, event_bus):
        registry.register(Tool("ping", "Ping", {}, lambda a: "pong"))

        tc = ToolCall(id="tc_1", name="ping", arguments="{}")
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="ok"),
        ]

        start_events = []
        complete_events = []
        event_bus.on(EventType.TOOL_START, lambda e: start_events.append(e.data))
        event_bus.on(EventType.TOOL_COMPLETE, lambda e: complete_events.append(e.data))

        await agent.chat("Go")
        assert len(start_events) == 1
        assert start_events[0]["name"] == "ping"
        assert len(complete_events) == 1
        assert complete_events[0]["result"] == "pong"


class TestInterrupt:
    @pytest.mark.asyncio
    async def test_interrupt_during_chat(self, agent, mock_provider, cancel_token):
        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1.0)
            return Response(content="Should not see this")

        mock_provider.call.side_effect = slow_call

        async def cancel_soon():
            await asyncio.sleep(0.05)
            cancel_token.cancel()

        asyncio.create_task(cancel_soon())
        result = await agent.chat("Hello")
        assert result == "[interrupted]"

    @pytest.mark.asyncio
    async def test_interrupt_fires_event(self, agent, mock_provider, event_bus, cancel_token):
        interrupted_events = []
        event_bus.on(EventType.INTERRUPTED, lambda e: interrupted_events.append(e.data))

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1.0)
            return Response(content="x")

        mock_provider.call.side_effect = slow_call

        async def cancel_soon():
            await asyncio.sleep(0.05)
            cancel_token.cancel()

        asyncio.create_task(cancel_soon())
        await agent.chat("Hello")
        assert len(interrupted_events) >= 1

    @pytest.mark.asyncio
    async def test_interrupt_during_tool_message_consistency(self, agent, mock_provider, registry, cancel_token):
        """Interrupt during 2nd tool: messages must have no orphaned tool_calls."""
        registry.register(Tool("echo", "Echo", {"x": "string"}, lambda a: "r-" + a["x"]))

        tc1 = ToolCall(id="tc_1", name="echo", arguments=json.dumps({"x": "1"}))
        tc2 = ToolCall(id="tc_2", name="echo", arguments=json.dumps({"x": "2"}))
        tc3 = ToolCall(id="tc_3", name="echo", arguments=json.dumps({"x": "3"}))
        mock_provider.call.return_value = Response(content="", tool_calls=[tc1, tc2, tc3])

        call_count = 0
        original_run = agent._run_tool_async

        async def controlled_run(name, args):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                cancel_token.cancel()
            return await original_run(name, args)

        agent._run_tool_async = controlled_run
        result = await agent.chat("Go")
        assert result == "[interrupted]"

        # Verify: every tool_call in assistant messages must have a matching tool result
        for msg in agent.messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                tc_ids = {tc["id"] for tc in msg["tool_calls"]}
                result_ids = {
                    m["tool_call_id"]
                    for m in agent.messages
                    if m.get("role") == "tool"
                }
                assert tc_ids.issubset(result_ids), f"Orphaned tool_calls: {tc_ids - result_ids}"

    @pytest.mark.asyncio
    async def test_interrupt_emits_synthetic_tool_complete(self, agent, mock_provider, registry, event_bus, cancel_token):
        """Interrupted tool should get a synthetic TOOL_COMPLETE event."""
        registry.register(Tool("echo", "Echo", {}, lambda a: "ok"))

        tc1 = ToolCall(id="tc_1", name="echo", arguments="{}")
        tc2 = ToolCall(id="tc_2", name="echo", arguments="{}")
        mock_provider.call.return_value = Response(content="", tool_calls=[tc1, tc2])

        complete_events = []
        event_bus.on(EventType.TOOL_COMPLETE, lambda e: complete_events.append(e.data))

        original_run = agent._run_tool_async

        async def controlled_run(name, args):
            cancel_token.cancel()
            return await original_run(name, args)

        agent._run_tool_async = controlled_run
        await agent.chat("Go")

        assert any(e["result"] == "[interrupted]" for e in complete_events)

    @pytest.mark.asyncio
    async def test_interrupt_resets_token(self, agent, mock_provider, cancel_token):
        """After interrupt, token should be reset (ready for next chat)."""
        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1.0)
            return Response(content="x")

        mock_provider.call.side_effect = slow_call

        async def cancel_soon():
            await asyncio.sleep(0.05)
            cancel_token.cancel()

        asyncio.create_task(cancel_soon())
        await agent.chat("Hello")
        assert not cancel_token.is_cancelled


class TestPermission:
    @pytest.mark.asyncio
    async def test_permission_deny(self, agent, mock_provider, registry):
        checker = PermissionChecker(permission_config={"*": "deny"})
        agent.permission_checker = checker

        registry.register(Tool("secret", "Secret", {}, lambda a: "hidden"))
        tc = ToolCall(id="tc_1", name="secret", arguments="{}")
        mock_provider.call.return_value = Response(tool_calls=[tc])

        result = await agent.chat("Use secret tool")
        assert result == "[interrupted]"

    @pytest.mark.asyncio
    async def test_permission_user_input(self, agent, mock_provider, registry):
        class CustomHandler:
            async def ask_permission(self, tool_name, tool_args):
                return "my custom answer"

        checker = PermissionChecker(
            permission_config={"*": "ask"},
            handler=CustomHandler(),
        )
        agent.permission_checker = checker

        registry.register(Tool("ask_tool", "Ask", {}, lambda a: "real result"))
        tc = ToolCall(id="tc_1", name="ask_tool", arguments="{}")
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="Done"),
        ]

        result = await agent.chat("Use ask tool")
        assert result == "Done"


class TestAgentOperations:
    @pytest.mark.asyncio
    async def test_clear(self, agent, mock_provider):
        mock_provider.call.return_value = Response(content="ok")
        await agent.chat("Hello")
        assert len(agent.messages) > 0
        agent.clear()
        assert len(agent.messages) == 0

    def test_update_provider(self, agent):
        new_provider = AsyncMock()
        agent.update_provider(new_provider)
        assert agent.provider is new_provider

    @pytest.mark.asyncio
    async def test_usage_tracking(self, agent, mock_provider):
        mock_provider.call.return_value = Response(
            content="ok",
            usage=Usage(prompt_tokens=100, completion_tokens=50),
        )
        await agent.chat("Hello")
        assert agent.last_usage is not None
        assert agent.last_usage.prompt_tokens == 100

    @pytest.mark.asyncio
    async def test_tool_result_truncation(self, mock_provider, registry, event_bus, cancel_token):
        config = Config.from_dict({
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "tool_result_limit": 10,
        })
        agent = Agent(
            provider=mock_provider,
            system_prompt="test",
            tools=registry,
            event_bus=event_bus,
            cancel_token=cancel_token,
            permission_checker=None,
            config=config,
        )

        registry.register(Tool("long", "Long output", {}, lambda a: "a" * 1000))
        tc = ToolCall(id="tc_1", name="long", arguments="{}")
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="ok"),
        ]

        complete_events = []
        event_bus.on(EventType.TOOL_COMPLETE, lambda e: complete_events.append(e.data))
        await agent.chat("Go")
        result_text = complete_events[0]["result"]
        assert "truncated" in result_text


class TestBuildUserContent:
    def test_text_only(self):
        result = Agent._build_user_content("hello", [])
        assert result == "hello"

    def test_no_media_returns_text(self):
        result = Agent._build_user_content("hello", ["/nonexistent/path.png"])
        assert isinstance(result, str) or isinstance(result, list)
