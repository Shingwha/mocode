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
        keep_recent_turns=4,  # 保留最近 4 个轮次（user 消息数）
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
        manager.update_usage(5000)
        assert manager.last_prompt_tokens == 5000

    def test_should_compact_below_threshold(self, manager):
        manager.update_usage(50000)  # 50000 / 128000 = 39%
        assert not manager.should_compact("glm-5")

    def test_should_compact_above_threshold(self, manager):
        manager.update_usage(110000)  # 110000 / 128000 = 86%
        assert manager.should_compact("glm-5")

    def test_should_not_compact_when_disabled(self, manager):
        manager._config.enabled = False
        manager.update_usage(200000)
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


# ---- Turn-based splitting ----


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

    def test_with_tool_calls(self):
        msgs = [
            {"role": "user", "content": "read"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "file"},
            {"role": "user", "content": "edit"},
            {"role": "assistant", "content": "done"},
        ]
        result = CompactManager._find_turn_starts(msgs)
        assert result == [0, 3]

    def test_empty_messages(self):
        assert CompactManager._find_turn_starts([]) == []

    def test_no_user_messages(self):
        msgs = [
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "content": "result"},
        ]
        assert CompactManager._find_turn_starts(msgs) == []


# ---- Tool message stripping ----


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

    def test_removes_assistant_with_tool_calls(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}]},
        ]
        result = CompactManager._strip_tool_messages(msgs)
        assert len(result) == 0

    def test_keeps_plain_assistant(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = CompactManager._strip_tool_messages(msgs)
        assert result == msgs

    def test_empty_list(self):
        assert CompactManager._strip_tool_messages([]) == []

    def test_mixed_messages(self):
        msgs = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "checking", "tool_calls": [{"function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "data"},
            {"role": "assistant", "content": "Here's what I found"},
            {"role": "user", "content": "thanks"},
            {"role": "assistant", "content": "welcome"},
        ]
        result = CompactManager._strip_tool_messages(msgs)
        assert len(result) == 4
        assert all(
            msg["role"] != "tool" and not (msg["role"] == "assistant" and msg.get("tool_calls"))
            for msg in result
        )


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

    def test_tool_message_not_truncated(self):
        long_content = "x" * 3000
        msgs = [{"role": "tool", "content": long_content}]
        text = CompactManager._format_messages_for_summary(msgs)
        assert "[truncated]" not in text
        assert "x" * 3000 in text  # Full content preserved

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

    def test_tool_args_not_truncated(self):
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
        assert "..." not in text
        assert "x" * 300 in text  # Full args preserved


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

        msgs = _make_messages(6)  # 6 turns (12 messages), keep_recent_turns=4
        result = await manager.compact(msgs, "glm-5")

        # Should have: summary user + summary assistant + 4 turns * 2 msgs = 10
        # Turns 2,3,4,5 kept (8 msgs) + 2 summary = 10
        assert len(result) == 10
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Understood, I will continue based on the summary."
        # Recent turns preserved: turns 2,3,4,5 (user+assistant pairs)
        assert result[2]["content"] == "User message 2"
        assert result[3]["content"] == "Assistant response 2"
        assert result[4]["content"] == "User message 3"
        assert result[5]["content"] == "Assistant response 3"
        assert result[6]["content"] == "User message 4"
        assert result[7]["content"] == "Assistant response 4"
        assert result[8]["content"] == "User message 5"
        assert result[9]["content"] == "Assistant response 5"

    @pytest.mark.asyncio
    async def test_compact_few_turns(self, manager, mock_provider):
        msgs = _make_messages(2)  # 2 turns, keep_recent_turns=4
        result = await manager.compact(msgs, "glm-5")
        assert result is msgs  # Returns same list
        mock_provider.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_compact_exact_turns(self, manager, mock_provider):
        msgs = _make_messages(4)  # 4 turns, keep_recent_turns=4 — not enough to compress
        result = await manager.compact(msgs, "glm-5")
        assert result is msgs
        mock_provider.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_compact_resets_usage(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response("summary")
        manager.update_usage(100000)

        msgs = _make_messages(6)
        await manager.compact(msgs, "glm-5")
        assert manager.last_prompt_tokens == 0

    @pytest.mark.asyncio
    async def test_compact_emits_event(self, manager, mock_provider, event_bus):
        mock_provider.call.return_value = _make_summary_response("summary")

        events = []
        event_bus.on(EventType.CONTEXT_COMPACT, lambda e: events.append(e.data))

        msgs = _make_messages(6)  # 6 turns = 12 messages, keep 4 turns = 8 msgs
        result = await manager.compact(msgs, "glm-5")

        assert len(events) == 1
        assert events[0]["old_count"] == 12
        assert events[0]["new_count"] == len(result)
        assert events[0]["compressed_count"] == 12 - len(result)

    @pytest.mark.asyncio
    async def test_compact_uses_fallback_on_failure(self, manager, mock_provider):
        mock_provider.call.side_effect = Exception("API down")

        msgs = _make_messages(6)
        result = await manager.compact(msgs, "glm-5")

        assert len(result) >= 2
        assert "[Context Summary]" in result[0]["content"]
        assert "messages compressed" in result[0]["content"]  # fallback text

    @pytest.mark.asyncio
    async def test_compact_strips_tool_messages_in_recent(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response("tool summary")

        # 5 turns: tool-heavy session
        msgs = [
            {"role": "user", "content": "Read file"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read", "arguments": '{"path": "a.py"}'}}]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "file content"},
            {"role": "assistant", "content": "Here's the file"},
            {"role": "user", "content": "Edit it"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "write", "arguments": '{"path": "a.py", "content": "..."}'}}]},
            {"role": "tool", "tool_call_id": "tc_2", "content": "written"},
            {"role": "assistant", "content": "Done editing"},
            {"role": "user", "content": "Run tests"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash", "arguments": '{"command": "pytest"}'}}]},
            {"role": "tool", "tool_call_id": "tc_3", "content": "all passed"},
            {"role": "assistant", "content": "All tests pass"},
        ]
        # 4 user turns, keep_recent_turns=4 → not enough to compress (4 turns <= 4)
        result = await manager.compact(msgs, "glm-5")
        assert result is msgs  # Not enough turns to compress

    @pytest.mark.asyncio
    async def test_compact_strips_tools_when_enough_turns(self, manager, mock_provider):
        mock_provider.call.return_value = _make_summary_response("summary with tool info")

        # 6 turns with tool calls in recent turns
        msgs = [
            {"role": "user", "content": "Turn 0"},
            {"role": "assistant", "content": "Response 0"},
            {"role": "user", "content": "Turn 1"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "data"},
            {"role": "assistant", "content": "Found it"},
            {"role": "user", "content": "Turn 2"},
            {"role": "assistant", "content": "Response 2"},
            {"role": "user", "content": "Turn 3"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc_2", "content": "output"},
            {"role": "assistant", "content": "Ran it"},
            {"role": "user", "content": "Turn 4"},
            {"role": "assistant", "content": "Response 4"},
            {"role": "user", "content": "Turn 5"},
            {"role": "assistant", "content": "Response 5"},
        ]
        result = await manager.compact(msgs, "glm-5")

        # Summary + 4 turns kept, tool messages stripped from recent
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]

        # No tool messages or assistant+tool_calls in recent portion
        recent = result[2:]
        for msg in recent:
            assert msg["role"] != "tool"
            assert not (msg["role"] == "assistant" and msg.get("tool_calls"))

        # Should have user/assistant pairs only (tool msgs and assistant+tool_calls stripped)
        assert recent[0] == {"role": "user", "content": "Turn 2"}
        assert recent[1] == {"role": "assistant", "content": "Response 2"}
        assert recent[2] == {"role": "user", "content": "Turn 3"}
        # "Ran it" is a plain assistant (no tool_calls) so it's kept
        assert recent[3] == {"role": "assistant", "content": "Ran it"}
        assert recent[4] == {"role": "user", "content": "Turn 4"}
        assert recent[5] == {"role": "assistant", "content": "Response 4"}
        assert recent[6] == {"role": "user", "content": "Turn 5"}
        assert recent[7] == {"role": "assistant", "content": "Response 5"}

    @pytest.mark.asyncio
    async def test_compact_split_point_on_user_boundary(self, compact_config, mock_provider, event_bus):
        """split_point always lands on a user message"""
        compact_config.keep_recent_turns = 2
        manager = CompactManager(compact_config, mock_provider, event_bus)
        mock_provider.call.return_value = _make_summary_response("summary")

        msgs = _make_messages(5)  # 5 turns, keep 2
        result = await manager.compact(msgs, "glm-5")

        # First message after summary pair should be user
        assert result[2]["role"] == "user"
        assert result[2]["content"] == "User message 3"

    @pytest.mark.asyncio
    async def test_compact_all_when_keep_zero(self, compact_config, mock_provider, event_bus):
        """keep_recent_turns=0 时压缩全部消息，不保留任何原始消息"""
        compact_config.keep_recent_turns = 0
        manager = CompactManager(compact_config, mock_provider, event_bus)
        mock_provider.call.return_value = _make_summary_response("full summary")

        msgs = _make_messages(3)  # 3 turns
        result = await manager.compact(msgs, "glm-5")

        # 应该只有 2 条消息：摘要 + 确认，没有任何原始消息
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert "full summary" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Understood, I will continue based on the summary."

    @pytest.mark.asyncio
    async def test_compact_summary_covers_all_messages(self, manager, mock_provider):
        """摘要应从完整消息列表生成，包含 recent 中的内容"""
        mock_provider.call.return_value = _make_summary_response("comprehensive summary")

        msgs = _make_messages(6)
        await manager.compact(msgs, "glm-5")

        # _generate_summary should receive formatted text of ALL messages
        call_args = mock_provider.call.call_args
        formatted_text = call_args[1]["messages"][0]["content"]
        # Should include recent turns in the formatted text
        assert "User message 5" in formatted_text
        assert "Assistant response 0" in formatted_text


# ---- CompactConfig integration ----


class TestCompactConfig:
    def test_default_values(self):
        cfg = CompactConfig()
        assert cfg.enabled is True
        assert cfg.threshold == 0.80
        assert cfg.keep_recent_turns == 1
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
        assert cfg.compact.keep_recent_turns == 1  # default preserved

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
