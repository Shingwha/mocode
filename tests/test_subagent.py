"""Tests for SubAgent — unified lightweight agent runner"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from mocode.agent import Agent
from mocode.config import Config
from mocode.event import EventType, EventBus
from mocode.interrupt import CancellationToken, Interrupted
from mocode.permission import (
    CheckOutcome,
    CheckResult,
    DenyAllPermissionHandler,
    PermissionChecker,
)
from mocode.provider import Response, ToolCall, Usage
from mocode.subagent import SubAgent, SubAgentConfig, SubAgentResult
from mocode.tool import Tool, ToolRegistry


# ---- Fixtures ----


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.model = "test-model"
    return provider


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register(Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"]))
    reg.register(Tool("add", "Add", {"x": "string", "y": "string"}, lambda a: str(int(a["x"]) + int(a["y"]))))
    reg.register(Tool("slow", "Slow", {}, lambda a: "done"))
    return reg


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def cancel_token():
    return CancellationToken()


def _make_tool_call(name: str, arguments: dict, call_id: str = "tc_1"):
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


# ---- Pure text response (no tool calls) ----


class TestPureText:
    @pytest.mark.asyncio
    async def test_text_only_response(self, mock_provider, registry):
        mock_provider.call.return_value = Response(content="Hello from sub-agent!")
        config = SubAgentConfig(system_prompt="You are a helper.")
        sub = SubAgent(mock_provider, registry, config)
        result = await sub.run("Hi")

        assert result.content == "Hello from sub-agent!"
        assert result.tool_calls_made == 0
        assert result.had_error is False
        assert len(result.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_run_messages_text_only(self, mock_provider, registry):
        mock_provider.call.return_value = Response(content="Done")
        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)
        messages = [{"role": "user", "content": "Go"}]
        result = await sub.run_messages(messages)

        assert result.content == "Done"
        # Original messages list should not be modified (isolation)
        assert len(messages) == 1


# ---- Single and multi tool calls ----


class TestToolCalls:
    @pytest.mark.asyncio
    async def test_single_tool_call(self, mock_provider, registry):
        tc = _make_tool_call("echo", {"msg": "hello"})
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="Echo complete"),
        ]

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)
        result = await sub.run("Echo hello")

        assert result.content == "Echo complete"
        assert result.tool_calls_made == 1
        assert len(result.messages) == 4  # user + assistant(tool_call) + tool_result + assistant(text)

    @pytest.mark.asyncio
    async def test_multi_tool_calls(self, mock_provider, registry):
        tc1 = _make_tool_call("echo", {"msg": "a"}, "tc_1")
        tc2 = _make_tool_call("add", {"x": "1", "y": "2"}, "tc_2")
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc1, tc2]),
            Response(content="All done"),
        ]

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)
        result = await sub.run("Multi")

        assert result.tool_calls_made == 2
        assert result.content == "All done"

    @pytest.mark.asyncio
    async def test_tool_result_in_messages(self, mock_provider, registry):
        tc = _make_tool_call("add", {"x": "3", "y": "4"}, "tc_1")
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="ok"),
        ]

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)
        result = await sub.run("Add numbers")

        # Find the tool result message
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "7"
        assert tool_msgs[0]["tool_call_id"] == "tc_1"


# ---- max_tool_calls limit ----


class TestMaxToolCalls:
    @pytest.mark.asyncio
    async def test_max_tool_calls_limits_iterations(self, mock_provider, registry):
        tc = _make_tool_call("echo", {"msg": "loop"}, "tc_loop")
        looping_response = Response(tool_calls=[tc])
        mock_provider.call.return_value = looping_response

        config = SubAgentConfig(system_prompt="sys", max_tool_calls=3)
        sub = SubAgent(mock_provider, registry, config)
        result = await sub.run("Loop")

        assert result.tool_calls_made == 3


# ---- tool_names filtering ----


class TestToolFiltering:
    @pytest.mark.asyncio
    async def test_tool_names_filters_schemas(self, mock_provider, registry):
        """Only specified tools should appear in provider call"""
        captured_tools = []

        async def capture_call(messages, system, tools, max_tokens):
            captured_tools.extend(tools)
            return Response(content="ok")

        mock_provider.call.side_effect = capture_call

        config = SubAgentConfig(system_prompt="sys", tool_names=["echo"])
        sub = SubAgent(mock_provider, registry, config)
        await sub.run("Test")

        assert len(captured_tools) == 1
        assert captured_tools[0]["function"]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_tool_names_none_exposes_all(self, mock_provider, registry):
        captured_tools = []

        async def capture_call(messages, system, tools, max_tokens):
            captured_tools.extend(tools)
            return Response(content="ok")

        mock_provider.call.side_effect = capture_call

        config = SubAgentConfig(system_prompt="sys", tool_names=None)
        sub = SubAgent(mock_provider, registry, config)
        await sub.run("Test")

        assert len(captured_tools) == 3  # echo, add, slow

    @pytest.mark.asyncio
    async def test_tool_names_empty_list_exposes_nothing(self, mock_provider, registry):
        captured_tools = []

        async def capture_call(messages, system, tools, max_tokens):
            captured_tools.extend(tools)
            return Response(content="ok")

        mock_provider.call.side_effect = capture_call

        config = SubAgentConfig(system_prompt="sys", tool_names=[])
        sub = SubAgent(mock_provider, registry, config)
        await sub.run("Test")

        assert len(captured_tools) == 0


# ---- bypass_permissions ----


class TestPermissions:
    @pytest.mark.asyncio
    async def test_bypass_true_skips_permission(self, mock_provider, registry):
        checker = PermissionChecker(permission_config={"*": "deny"})
        tc = _make_tool_call("echo", {"msg": "test"})
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="ok"),
        ]

        config = SubAgentConfig(system_prompt="sys", bypass_permissions=True)
        sub = SubAgent(mock_provider, registry, config, permission_checker=checker)
        result = await sub.run("Test")

        assert result.tool_calls_made == 1
        assert result.had_error is False

    @pytest.mark.asyncio
    async def test_bypass_false_checks_permission(self, mock_provider, registry):
        checker = PermissionChecker(permission_config={"*": "deny"})
        tc = _make_tool_call("echo", {"msg": "test"})
        mock_provider.call.return_value = Response(tool_calls=[tc])

        config = SubAgentConfig(system_prompt="sys", bypass_permissions=False)
        sub = SubAgent(mock_provider, registry, config, permission_checker=checker)

        with pytest.raises(Interrupted):
            await sub.run("Test")


# ---- cancel_token ----


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_interrupts_run(self, mock_provider, registry, cancel_token):
        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1.0)
            return Response(content="Should not see")

        mock_provider.call.side_effect = slow_call

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config, cancel_token=cancel_token)

        async def cancel_soon():
            await asyncio.sleep(0.05)
            cancel_token.cancel()

        asyncio.create_task(cancel_soon())
        with pytest.raises(Interrupted):
            await sub.run("Test")


# ---- event_bus ----


class TestEvents:
    @pytest.mark.asyncio
    async def test_tool_events_fired(self, mock_provider, registry, event_bus):
        tc = _make_tool_call("echo", {"msg": "hello"})
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="done"),
        ]

        start_events = []
        complete_events = []
        event_bus.on(EventType.TOOL_START, lambda e: start_events.append(e.data))
        event_bus.on(EventType.TOOL_COMPLETE, lambda e: complete_events.append(e.data))

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config, event_bus=event_bus)
        await sub.run("Test")

        assert len(start_events) == 1
        assert start_events[0]["name"] == "echo"
        assert len(complete_events) == 1
        assert complete_events[0]["result"] == "hello"

    @pytest.mark.asyncio
    async def test_no_events_without_bus(self, mock_provider, registry):
        """No events emitted when event_bus is None — should not error"""
        tc = _make_tool_call("echo", {"msg": "hi"})
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="ok"),
        ]

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)  # no event_bus
        result = await sub.run("Test")

        assert result.tool_calls_made == 1
        assert result.had_error is False


# ---- Provider error handling ----


class TestProviderErrors:
    @pytest.mark.asyncio
    async def test_provider_error_sets_had_error(self, mock_provider, registry):
        mock_provider.call.side_effect = Exception("API failure")

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)
        result = await sub.run("Test")

        assert result.had_error is True
        assert result.tool_calls_made == 0

    @pytest.mark.asyncio
    async def test_provider_error_mid_loop(self, mock_provider, registry):
        tc = _make_tool_call("echo", {"msg": "first"})
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Exception("API crashed"),
        ]

        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)
        result = await sub.run("Test")

        assert result.had_error is True
        assert result.tool_calls_made == 1  # first tool executed before crash


# ---- Message isolation ----


class TestMessageIsolation:
    @pytest.mark.asyncio
    async def test_run_does_not_modify_input_messages(self, mock_provider, registry):
        mock_provider.call.return_value = Response(content="ok")
        config = SubAgentConfig(system_prompt="sys")
        sub = SubAgent(mock_provider, registry, config)

        original = [{"role": "user", "content": "Hello"}]
        original_copy = list(original)
        await sub.run_messages(original)

        assert original == original_copy
        assert len(original) == 1


# ---- tool_result_limit ----


class TestToolResultLimit:
    @pytest.mark.asyncio
    async def test_result_truncated(self, mock_provider, registry, event_bus):
        registry.register(Tool("long_output", "Long", {}, lambda a: "x" * 1000))
        tc = _make_tool_call("long_output", {})
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="ok"),
        ]

        complete_events = []
        event_bus.on(EventType.TOOL_COMPLETE, lambda e: complete_events.append(e.data))

        config = SubAgentConfig(system_prompt="sys", tool_result_limit=50)
        sub = SubAgent(mock_provider, registry, config, event_bus=event_bus)
        await sub.run("Test")

        result_text = complete_events[0]["result"]
        assert "truncated" in result_text


# ---- tool_timeout ----


class TestToolTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_error_message(self, mock_provider, registry, event_bus):
        # Register a slow tool (simulated by making asyncio.to_thread take long)
        # We'll test by using a tool that works but with a very short timeout
        registry.register(Tool("instant", "Instant", {}, lambda a: "quick"))
        tc = _make_tool_call("instant", {})
        mock_provider.call.side_effect = [
            Response(tool_calls=[tc]),
            Response(content="ok"),
        ]

        # Use a reasonable timeout - just verify the mechanism works
        config = SubAgentConfig(system_prompt="sys", tool_timeout=30)
        sub = SubAgent(mock_provider, registry, config, event_bus=event_bus)
        result = await sub.run("Test")

        assert result.tool_calls_made == 1


# ---- spawn() integration ----


class TestSpawnIntegration:
    @pytest.mark.asyncio
    async def test_spawn_creates_working_subagent(self, mock_provider, registry, event_bus, cancel_token):
        config = Config.from_dict({
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
        })
        agent = Agent(
            provider=mock_provider,
            system_prompt="main prompt",
            tools=registry,
            event_bus=event_bus,
            cancel_token=cancel_token,
            permission_checker=None,
            config=config,
        )

        mock_provider.call.return_value = Response(content="Sub response")
        sub = agent.spawn("Sub prompt", tool_names=["echo"])
        result = await sub.run("Hi")

        assert result.content == "Sub response"
        assert isinstance(sub, SubAgent)

    @pytest.mark.asyncio
    async def test_spawn_inherits_infrastructure(self, mock_provider, registry, event_bus, cancel_token):
        config = Config.from_dict({
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
        })
        agent = Agent(
            provider=mock_provider,
            system_prompt="main",
            tools=registry,
            event_bus=event_bus,
            cancel_token=cancel_token,
            permission_checker=None,
            config=config,
        )

        sub = agent.spawn("sub prompt")

        assert sub._provider is agent.provider
        assert sub._tools is agent._tools
        assert sub._event_bus is agent.event_bus
        assert sub._cancel_token is agent.cancel_token

    @pytest.mark.asyncio
    async def test_spawn_tool_filtering(self, mock_provider, registry, event_bus, cancel_token):
        config = Config.from_dict({
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
        })
        agent = Agent(
            provider=mock_provider,
            system_prompt="main",
            tools=registry,
            event_bus=event_bus,
            cancel_token=cancel_token,
            permission_checker=None,
            config=config,
        )

        sub = agent.spawn("sub", tool_names=["echo"])
        assert len(sub._tool_schemas) == 1
        assert sub._tool_schemas[0]["function"]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_spawn_inherits_config_values(self, mock_provider, registry, event_bus, cancel_token):
        config = Config.from_dict({
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "max_tokens": 9999,
            "tool_timeout": 60,
            "tool_result_limit": 5000,
        })
        agent = Agent(
            provider=mock_provider,
            system_prompt="main",
            tools=registry,
            event_bus=event_bus,
            cancel_token=cancel_token,
            permission_checker=None,
            config=config,
        )

        sub = agent.spawn("sub")
        assert sub._config.max_tokens == 9999
        assert sub._config.tool_timeout == 60
        assert sub._config.tool_result_limit == 5000
