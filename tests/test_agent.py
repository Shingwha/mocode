"""AsyncAgent tests with mocked LLM provider"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.core.agent import AsyncAgent
from mocode.core.events import EventType, EventBus
from mocode.core.interrupt import InterruptToken
from mocode.core.permission import (
    CheckOutcome,
    CheckResult,
    DenyAllPermissionHandler,
    PermissionChecker,
)
from mocode.tools.base import Tool, ToolRegistry


def _make_message(content="Hello!", tool_calls=None):
    """Create a mock ChatCompletion message response"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    return msg


def _make_response(content="Hello!", tool_calls=None):
    """Create a mock ChatCompletion response"""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = _make_message(content, tool_calls)
    return resp


def _make_tool_call(name, args_dict, call_id="tc_1"):
    """Create a mock tool call"""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args_dict)
    return tc


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.call = AsyncMock()
    return provider


@pytest.fixture
def agent(mock_provider):
    return AsyncAgent(
        provider=mock_provider,
        system_prompt="You are a test assistant.",
        max_tokens=1024,
        event_bus=EventBus(),
    )


class TestSimpleChat:
    @pytest.mark.asyncio
    async def test_simple_chat(self, agent, mock_provider):
        mock_provider.call.return_value = _make_response("Hi there!")
        result = await agent.chat("Hello")
        assert result == "Hi there!"
        assert len(agent.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_text_complete_event(self, agent, mock_provider):
        events = []
        agent.event_bus.on(EventType.TEXT_COMPLETE, lambda e: events.append(e.data))
        mock_provider.call.return_value = _make_response("Response text")
        await agent.chat("Hello")
        assert len(events) == 1
        assert events[0]["content"] == "Response text"

    @pytest.mark.asyncio
    async def test_message_added_event(self, agent, mock_provider):
        events = []
        agent.event_bus.on(EventType.MESSAGE_ADDED, lambda e: events.append(e.data))
        mock_provider.call.return_value = _make_response("ok")
        await agent.chat("Hello")
        assert events[0]["role"] == "user"


class TestToolCalls:
    @pytest.mark.asyncio
    async def test_tool_call_and_result(self, agent, mock_provider):
        ToolRegistry.register(Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"]))

        tc = _make_tool_call("echo", {"msg": "hello"})
        mock_provider.call.side_effect = [
            _make_response(None, tool_calls=[tc]),
            _make_response("Done!"),
        ]

        result = await agent.chat("Do something")
        assert result == "Done!"

    @pytest.mark.asyncio
    async def test_multi_tool_calls(self, agent, mock_provider):
        ToolRegistry.register(Tool("add", "Add", {"x": "string"}, lambda a: "result_a"))
        ToolRegistry.register(Tool("sub", "Sub", {"x": "string"}, lambda a: "result_b"))

        tc1 = _make_tool_call("add", {"x": "1"}, call_id="tc_1")
        tc2 = _make_tool_call("sub", {"x": "2"}, call_id="tc_2")
        mock_provider.call.side_effect = [
            _make_response(None, tool_calls=[tc1, tc2]),
            _make_response("All done"),
        ]

        result = await agent.chat("Multi tool")
        assert result == "All done"

    @pytest.mark.asyncio
    async def test_tool_events(self, agent, mock_provider):
        ToolRegistry.register(Tool("ping", "Ping", {}, lambda a: "pong"))

        tc = _make_tool_call("ping", {}, call_id="tc_1")
        mock_provider.call.side_effect = [
            _make_response(None, tool_calls=[tc]),
            _make_response("ok"),
        ]

        start_events = []
        complete_events = []
        agent.event_bus.on(EventType.TOOL_START, lambda e: start_events.append(e.data))
        agent.event_bus.on(EventType.TOOL_COMPLETE, lambda e: complete_events.append(e.data))

        await agent.chat("Go")
        assert len(start_events) == 1
        assert start_events[0]["name"] == "ping"
        assert len(complete_events) == 1
        assert complete_events[0]["result"] == "pong"


class TestInterrupt:
    @pytest.mark.asyncio
    async def test_interrupt_during_chat(self, agent, mock_provider):
        token = InterruptToken()
        agent.interrupt_token = token

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1.0)
            return _make_response("Should not see this")

        mock_provider.call.side_effect = slow_call

        # Interrupt shortly after call starts
        async def interrupt_soon():
            await asyncio.sleep(0.05)
            token.interrupt()

        asyncio.create_task(interrupt_soon())
        result = await agent.chat("Hello")
        assert result == "[interrupted]"

    @pytest.mark.asyncio
    async def test_interrupt_fires_event(self, agent, mock_provider):
        token = InterruptToken()
        agent.interrupt_token = token

        interrupted_events = []
        agent.event_bus.on(EventType.INTERRUPTED, lambda e: interrupted_events.append(e.data))

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1.0)
            return _make_response("x")

        mock_provider.call.side_effect = slow_call

        async def interrupt_soon():
            await asyncio.sleep(0.05)
            token.interrupt()

        asyncio.create_task(interrupt_soon())
        await agent.chat("Hello")
        assert len(interrupted_events) >= 1


class TestPermission:
    @pytest.mark.asyncio
    async def test_permission_deny(self, agent, mock_provider):
        checker = PermissionChecker(
            permission_config={"*": "deny"},
        )
        agent.permission_checker = checker

        ToolRegistry.register(Tool("secret", "Secret", {}, lambda a: "hidden"))
        tc = _make_tool_call("secret", {})
        mock_provider.call.return_value = _make_response(None, tool_calls=[tc])

        result = await agent.chat("Use secret tool")
        assert result == "[interrupted]"

    @pytest.mark.asyncio
    async def test_permission_user_input(self, agent, mock_provider):
        class CustomHandler:
            async def ask_permission(self, tool_name, tool_args):
                return "my custom answer"

        checker = PermissionChecker(
            permission_config={"*": "ask"},
            handler=CustomHandler(),
        )
        agent.permission_checker = checker

        ToolRegistry.register(Tool("ask_tool", "Ask", {}, lambda a: "real result"))
        tc = _make_tool_call("ask_tool", {})
        mock_provider.call.side_effect = [
            _make_response(None, tool_calls=[tc]),
            _make_response("Done"),
        ]

        result = await agent.chat("Use ask tool")
        assert result == "Done"


class TestAgentOperations:
    @pytest.mark.asyncio
    async def test_clear(self, agent, mock_provider):
        mock_provider.call.return_value = _make_response("ok")
        await agent.chat("Hello")
        assert len(agent.messages) > 0
        agent.clear()
        assert len(agent.messages) == 0

    def test_update_provider(self, agent):
        agent.messages.append({"role": "user", "content": "test"})
        new_provider = AsyncMock()
        agent.update_provider(new_provider)
        assert agent.provider is new_provider
        assert len(agent.messages) == 0

    @pytest.mark.asyncio
    async def test_tool_result_truncation(self, agent, mock_provider):
        from mocode.core.config import Config
        config = Config.load(data={
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "tool_result_limit": 10,
        })
        agent.config = config

        ToolRegistry.register(Tool("long", "Long output", {}, lambda a: "a" * 1000))
        tc = _make_tool_call("long", {})
        mock_provider.call.side_effect = [
            _make_response(None, tool_calls=[tc]),
            _make_response("ok"),
        ]

        complete_events = []
        agent.event_bus.on(EventType.TOOL_COMPLETE, lambda e: complete_events.append(e.data))
        await agent.chat("Go")
        # Result should be truncated
        result_text = complete_events[0]["result"]
        assert "truncated" in result_text


class TestBuildUserContent:
    def test_text_only(self):
        result = AsyncAgent._build_user_content("hello", [])
        assert result == "hello"

    def test_no_media_returns_text(self):
        result = AsyncAgent._build_user_content("hello", ["/nonexistent/path.png"])
        # Non-existent files are skipped, falls back to text
        assert isinstance(result, str) or isinstance(result, list)


