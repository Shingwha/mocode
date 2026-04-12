"""Compact context compression tests"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mocode.core.compact import CompactManager, DEFAULT_CONTEXT_WINDOW
from mocode.core.config import CompactConfig
from mocode.core.events import EventBus, EventType


# ---- Fixtures ----


@pytest.fixture
def compact_config():
    return CompactConfig(
        enabled=True,
        threshold=0.80,
        keep_recent_turns=2,
    )


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.call = AsyncMock()
    return provider


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def manager(compact_config, mock_provider, event_bus):
    return CompactManager(compact_config, mock_provider, event_bus)


# ---- Helpers ----


def _make_messages(turns: int) -> list[dict]:
    """Create messages with N user turns (each user + assistant pair)"""
    messages = []
    for i in range(turns):
        messages.append({"role": "user", "content": f"User message {i}"})
        messages.append({"role": "assistant", "content": f"Assistant response {i}"})
    return messages


def _make_messages_with_tools() -> list[dict]:
    """Create messages including tool calls"""
    return [
        {"role": "user", "content": "Read file"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "read",
                        "arguments": '{"path": "/tmp/test.py"}',
                    }
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc_1", "content": "file content here"},
        {"role": "user", "content": "Now edit it"},
        {"role": "assistant", "content": "Edited!"},
        {"role": "user", "content": "Run it"},
        {"role": "assistant", "content": "Done!"},
    ]


def _make_summary_response(text: str) -> MagicMock:
    """Create a mock LLM response with summary text"""
    msg = MagicMock()
    msg.content = text
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = msg
    return resp


# ---- Token tracking ----


class TestTokenTracking:
    def test_update_usage(self, manager):
        manager.update_usage(5000, "glm-5")
        assert manager.last_prompt_tokens == 5000

    def test_reset_usage(self, manager):
        manager.update_usage(5000, "glm-5")
        manager.reset_usage()
        assert manager.last_prompt_tokens == 0

    def test_should_compact_below_threshold(self, manager):
        manager.update_usage(50000, "glm-5")  # 50000 / 128000 = 39%
        assert not manager.should_compact("glm-5")

    def test_should_compact_above_threshold(self, manager):
        manager.update_usage(110000, "glm-5")  # 110000 / 128000 = 86%
        assert manager.should_compact("glm-5")

    def test_should_not_compact_when_disabled(self, manager):
        manager._config.enabled = False
        manager.update_usage(200000, "glm-5")
        assert not manager.should_compact("glm-5")

    def test_should_not_compact_when_zero_tokens(self, manager):
        assert not manager.should_compact("glm-5")


class TestContextWindow:
    def test_known_model(self, manager):
        assert manager.get_context_window("glm-5") == 128000

    def test_unknown_model_uses_default(self, manager):
        assert manager.get_context_window("unknown-model") == DEFAULT_CONTEXT_WINDOW

    def test_custom_context_window(self, compact_config, mock_provider, event_bus):
        compact_config.context_windows = {"my-model": 64000}
        manager = CompactManager(compact_config, mock_provider, event_bus)
        assert manager.get_context_window("my-model") == 64000


# ---- Turn boundaries ----


class TestTurnBoundaries:
    def test_simple_messages(self):
        msgs = _make_messages(3)
        boundaries = CompactManager._find_turn_boundaries(msgs)
        assert boundaries == [0, 2, 4]

    def test_no_messages(self):
        assert CompactManager._find_turn_boundaries([]) == []

    def test_only_assistant_messages(self):
        msgs = [{"role": "assistant", "content": "hi"}]
        assert CompactManager._find_turn_boundaries(msgs) == []

    def test_with_tool_messages(self):
        msgs = _make_messages_with_tools()
        boundaries = CompactManager._find_turn_boundaries(msgs)
        assert boundaries == [0, 3, 5]


class TestToolSequenceIntegrity:
    def test_no_adjustment_needed(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
            {"role": "assistant", "content": "see ya"},
        ]
        result = CompactManager._ensure_no_partial_tool_sequence(msgs, 2)
        assert result == 2

    def test_split_at_tool_message(self):
        msgs = [
            {"role": "user", "content": "read file"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "read", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "tc_1", "content": "file content"},
            {"role": "user", "content": "edit file"},
            {"role": "assistant", "content": "edited"},
        ]
        # Split at index 2 (a tool message) — prev msg is assistant with tool_calls
        # so the method moves split forward past the tool message to keep sequence intact
        result = CompactManager._ensure_no_partial_tool_sequence(msgs, 2)
        assert result == 3  # Moved past tool message to keep sequence whole

    def test_empty_messages(self):
        result = CompactManager._ensure_no_partial_tool_sequence([], 0)
        assert result == 0


# ---- Formatting ----


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
                "tool_calls": [
                    {"function": {"name": "read", "arguments": '{"path": "test.py"}'}}
                ],
            }
        ]
        text = CompactManager._format_messages_for_summary(msgs)
        assert "[Assistant]" in text
        assert "[Tool Call: read" in text

    def test_tool_message_truncation(self):
        long_content = "x" * 3000
        msgs = [{"role": "tool", "content": long_content}]
        text = CompactManager._format_messages_for_summary(msgs)
        assert "[truncated]" in text
        assert len(text) < 2500  # Well under original 3000

    def test_multimodal_content(self):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    {"type": "text", "text": "Describe this image"},
                ],
            }
        ]
        text = CompactManager._format_messages_for_summary(msgs)
        assert "[image attached]" in text
        assert "Describe this image" in text

    def test_tool_args_truncation(self):
        long_args = '{"path": "' + "x" * 300 + '"}'
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "read", "arguments": long_args}}
                ],
            }
        ]
        text = CompactManager._format_messages_for_summary(msgs)
        assert "..." in text


# ---- Summary generation ----


class TestSummaryGeneration:
    @pytest.mark.asyncio
    async def test_generate_summary_success(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response(
            "[完成的决策]\n- Use stdlib csv"
        )
        result = await manager._generate_summary("some text")
        assert "stdlib csv" in result
        mock_provider.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_summary_failure(self, manager, mock_provider):
        mock_provider.call.side_effect = Exception("API error")
        result = await manager._generate_summary("some text")
        assert result == ""

    def test_fallback_summary(self):
        msgs = _make_messages(3)
        summary = CompactManager._build_fallback_summary(msgs)
        assert "6 messages compressed" in summary
        assert "User message 0" in summary
        assert "User message 2" in summary


# ---- Main compact operation ----


class TestCompact:
    @pytest.mark.asyncio
    async def test_compact_with_enough_turns(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response(
            "[完成的决策]\n- Read CSV file\n- Use stdlib"
        )

        msgs = _make_messages(6)  # 6 turns = 12 messages
        result = await manager.compact(msgs, "glm-5")

        # Should have: summary user + summary assistant + 2 recent turns (4 msgs)
        assert len(result) == 6
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Understood, I will continue based on the summary."
        # Recent messages preserved intact
        assert result[2]["content"] == "User message 4"
        assert result[3]["content"] == "Assistant response 4"

    @pytest.mark.asyncio
    async def test_compact_few_messages(self, manager, mock_provider):
        msgs = _make_messages(1)  # Only 2 messages
        result = await manager.compact(msgs, "glm-5")
        assert result is msgs  # Returns same list
        mock_provider.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_compact_few_turns(self, manager, mock_provider):
        msgs = _make_messages(2)  # 2 turns = 4 messages, keep_recent_turns=2
        result = await manager.compact(msgs, "glm-5")
        assert result is msgs  # Returns same list (not enough turns to compress)

    @pytest.mark.asyncio
    async def test_compact_resets_usage(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response("summary")
        manager.update_usage(100000, "glm-5")

        msgs = _make_messages(6)
        await manager.compact(msgs, "glm-5")
        assert manager.last_prompt_tokens == 0

    @pytest.mark.asyncio
    async def test_compact_emits_event(self, manager, mock_provider, event_bus):
        mock_provider.call.return_value = _make_summary_response("summary")

        events = []
        event_bus.on(EventType.CONTEXT_COMPACT, lambda e: events.append(e.data))

        msgs = _make_messages(6)
        await manager.compact(msgs, "glm-5")

        assert len(events) == 1
        assert events[0]["old_count"] == 12
        assert events[0]["new_count"] == 6
        assert events[0]["compressed_turns"] == 4

    @pytest.mark.asyncio
    async def test_compact_uses_fallback_on_failure(self, manager, mock_provider):
        mock_provider.call.side_effect = Exception("API down")

        msgs = _make_messages(6)
        result = await manager.compact(msgs, "glm-5")

        assert len(result) == 6
        assert "[Context Summary]" in result[0]["content"]
        assert "messages compressed" in result[0]["content"]  # fallback text

    @pytest.mark.asyncio
    async def test_compact_with_tool_messages(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response("tool summary")

        msgs = _make_messages_with_tools()
        result = await manager.compact(msgs, "glm-5")

        # Should still produce valid output
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        # Recent turns preserved
        assert "Run it" in result[-2]["content"]

    @pytest.mark.asyncio
    async def test_compact_preserves_recent_exact(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response("sum")

        msgs = _make_messages(5)  # 10 messages, 5 turns, keep 2
        result = await manager.compact(msgs, "glm-5")

        # Last 2 turns (4 messages) should be preserved exactly
        recent = result[2:]  # After summary user + summary assistant
        assert recent[0] == {"role": "user", "content": "User message 3"}
        assert recent[1] == {"role": "assistant", "content": "Assistant response 3"}
        assert recent[2] == {"role": "user", "content": "User message 4"}
        assert recent[3] == {"role": "assistant", "content": "Assistant response 4"}


# ---- CompactConfig integration ----


class TestCompactConfig:
    def test_default_values(self):
        cfg = CompactConfig()
        assert cfg.enabled is True
        assert cfg.threshold == 0.80
        assert cfg.keep_recent_turns == 4
        assert cfg.context_windows == {}

    def test_custom_values(self):
        cfg = CompactConfig(enabled=False, threshold=0.5, keep_recent_turns=2)
        assert cfg.enabled is False
        assert cfg.threshold == 0.5
        assert cfg.keep_recent_turns == 2

    def test_config_integration(self):
        from mocode.core.config import Config

        cfg = Config.load(data={
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "compact": {"enabled": False, "threshold": 0.9},
        })
        assert cfg.compact.enabled is False
        assert cfg.compact.threshold == 0.9
        assert cfg.compact.keep_recent_turns == 4  # default preserved

    def test_config_round_trip(self):
        from mocode.core.config import Config

        cfg = Config.load(data={
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "compact": {"threshold": 0.7},
        })
        cfg._persistence_enabled = False
        data = {
            "current": {"provider": "t", "model": "m"},
            "providers": {"t": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "compact": {"threshold": 0.7},
        }
        # Check serialization includes compact
        from dataclasses import asdict
        serialized = asdict(cfg.compact)
        assert serialized["threshold"] == 0.7
