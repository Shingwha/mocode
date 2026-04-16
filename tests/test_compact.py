"""Compact tests — v0.2 (Response DTO)"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mocode.compact import CompactManager, DEFAULT_CONTEXT_WINDOW
from mocode.config import CompactConfig
from mocode.event import EventBus, EventType
from mocode.provider import Response


@pytest.fixture
def compact_config():
    return CompactConfig(enabled=True, threshold=0.80, keep_recent_turns=4)


@pytest.fixture
def mock_provider():
    return AsyncMock()


@pytest.fixture
def manager(compact_config, mock_provider, event_bus):
    mgr = CompactManager(compact_config, mock_provider, event_bus)
    mgr._persist_summary_for_dream = MagicMock(return_value="")
    return mgr


def _make_messages(turns: int) -> list[dict]:
    messages = []
    for i in range(turns):
        messages.append({"role": "user", "content": f"User message {i}"})
        messages.append({"role": "assistant", "content": f"Assistant response {i}"})
    return messages


class TestTokenTracking:
    def test_update_usage(self, manager):
        manager.update_usage(5000)
        assert manager.last_prompt_tokens == 5000

    def test_should_compact_below_threshold(self, manager):
        manager.update_usage(50000)
        assert not manager.should_compact("glm-5")

    def test_should_compact_above_threshold(self, manager):
        manager.update_usage(110000)
        assert manager.should_compact("glm-5")

    def test_should_not_compact_when_disabled(self, manager):
        manager._config.enabled = False
        manager.update_usage(200000)
        assert not manager.should_compact("glm-5")


class TestContextWindow:
    def test_known_model(self, manager):
        assert manager.get_context_window("glm-5") == 128000

    def test_unknown_model_uses_default(self, manager):
        assert manager.get_context_window("unknown-model") == DEFAULT_CONTEXT_WINDOW


class TestFindTurnStarts:
    def test_basic_messages(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
            {"role": "assistant", "content": "see ya"},
        ]
        result = CompactManager._find_turn_starts(msgs)
        assert result == [0, 2]

    def test_empty_messages(self):
        assert CompactManager._find_turn_starts([]) == []


class TestStripToolMessages:
    def test_removes_tool_messages(self):
        msgs = [
            {"role": "user", "content": "read"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "file content"},
            {"role": "user", "content": "edit"},
            {"role": "assistant", "content": "done"},
        ]
        result = CompactManager._strip_tool_messages(msgs)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"


class TestFormatting:
    def test_user_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        text = CompactManager._format_messages_for_summary(msgs)
        assert "[User] hello" in text

    def test_assistant_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": "Let me check",
                "tool_calls": [{"function": {"name": "read", "arguments": '{"path": "test.py"}'}}],
            }
        ]
        text = CompactManager._format_messages_for_summary(msgs)
        assert "[Assistant]" in text
        assert "[Tool Call: read" in text


class TestCompact:
    @pytest.mark.asyncio
    async def test_compact_with_enough_turns(self, manager, mock_provider):
        mock_provider.call.return_value = Response(content="[完成的决策]\n- Use stdlib")

        msgs = _make_messages(6)
        result = await manager.compact(msgs, "glm-5")

        assert len(result) == 10
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_compact_few_turns(self, manager, mock_provider):
        msgs = _make_messages(2)
        result = await manager.compact(msgs, "glm-5")
        assert result is msgs
        mock_provider.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_compact_resets_usage(self, manager, mock_provider):
        mock_provider.call.return_value = Response(content="summary")
        manager.update_usage(100000)
        msgs = _make_messages(6)
        await manager.compact(msgs, "glm-5")
        assert manager.last_prompt_tokens == 0

    @pytest.mark.asyncio
    async def test_compact_emits_event(self, manager, mock_provider, event_bus):
        mock_provider.call.return_value = Response(content="summary")
        events = []
        event_bus.on(EventType.CONTEXT_COMPACT, lambda e: events.append(e.data))
        msgs = _make_messages(6)
        result = await manager.compact(msgs, "glm-5")
        assert len(events) == 1
        assert events[0]["old_count"] == 12

    @pytest.mark.asyncio
    async def test_compact_uses_fallback_on_failure(self, manager, mock_provider):
        mock_provider.call.side_effect = Exception("API down")
        msgs = _make_messages(6)
        result = await manager.compact(msgs, "glm-5")
        assert len(result) >= 2
        assert "[Context Summary]" in result[0]["content"]
