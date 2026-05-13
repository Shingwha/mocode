"""Tests for compact — CompactManager and CompactTool."""

import pytest

from mocode.core import Hooks, Response, Usage
from mocode.tools.compact import (
    CompactManager, CompactTool,
    find_turn_starts, strip_tool_messages, format_messages_for_summary,
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
        return Response(content="default response")


# ---- CompactManager ----


class TestCompactManager:
    def test_should_compact_false_when_no_usage(self):
        mgr = CompactManager(MockProvider())
        assert mgr.should_compact("test-model") is False

    def test_should_compact_false_below_threshold(self):
        mgr = CompactManager(MockProvider(), threshold=0.80)
        mgr.update_usage(10000)
        assert mgr.should_compact("test-model") is False

    def test_should_compact_true_above_threshold(self):
        mgr = CompactManager(MockProvider(), threshold=0.80)
        mgr.update_usage(110000)
        assert mgr.should_compact("test-model") is True

    def test_default_context_window(self):
        mgr = CompactManager(MockProvider())
        assert mgr.get_context_window("unknown-model") == 128_000

    def test_custom_context_windows(self):
        mgr = CompactManager(MockProvider(), context_windows={"gpt-4": 128000, "claude": 200000})
        assert mgr.get_context_window("gpt-4") == 128000
        assert mgr.get_context_window("claude") == 200000
        assert mgr.get_context_window("unknown") == 128_000

    def test_update_usage_and_reset(self):
        mgr = CompactManager(MockProvider())
        mgr.update_usage(50000)
        assert mgr.last_prompt_tokens == 50000
        mgr.reset()
        assert mgr.last_prompt_tokens == 0

    def test_model_property(self):
        provider = MockProvider(model="my-model")
        mgr = CompactManager(provider)
        assert mgr.model == "my-model"

    def test_update_provider(self):
        p1 = MockProvider(model="old")
        p2 = MockProvider(model="new")
        mgr = CompactManager(p1)
        assert mgr.model == "old"
        mgr.update_provider(p2)
        assert mgr.model == "new"

    @pytest.mark.asyncio
    async def test_compact_returns_original_when_few_turns(self):
        mgr = CompactManager(MockProvider(), keep_recent_turns=2)
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = await mgr.compact(messages, "test-model")
        assert result is messages

    @pytest.mark.asyncio
    async def test_compact_generates_summary(self):
        provider = MockProvider(responses=[Response(content="Summary of conversation")])
        bus = Hooks()
        events = []
        bus.on("context_compact", lambda data: events.append(data))

        mgr = CompactManager(provider, hooks=bus, keep_recent_turns=0)
        mgr.update_usage(100000)

        messages = [
            {"role": "user", "content": "Fix the auth bug"},
            {"role": "assistant", "content": "I fixed it in auth.py"},
            {"role": "user", "content": "Now add tests"},
            {"role": "assistant", "content": "Added test_auth.py"},
        ]
        result = await mgr.compact(messages, "test-model")

        # keep_recent_turns=0: all messages compressed, result = [summary_msg, ack_msg]
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert "Summary of conversation" in result[0]["content"]
        assert result[1]["role"] == "assistant"
        assert len(events) == 1
        assert events[0]["old_count"] == 4
        assert mgr.last_prompt_tokens == 0

    @pytest.mark.asyncio
    async def test_compact_fallback_summary(self):
        provider = MockProvider(responses=[Response(content="")])
        mgr = CompactManager(provider, keep_recent_turns=0)

        messages = [
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = await mgr.compact(messages, "test-model")

        # keep_recent_turns=0: result = [summary_msg, ack_msg]
        assert len(result) == 2
        assert "Conversation summary" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_compact_strips_tool_messages_from_recent(self):
        provider = MockProvider(responses=[Response(content="summary")])
        mgr = CompactManager(provider, keep_recent_turns=1)

        messages = [
            {"role": "user", "content": "old message"},
            {"role": "assistant", "content": "old response"},
            {"role": "user", "content": "run the tool"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "output"},
            {"role": "assistant", "content": "done"},
        ]
        result = await mgr.compact(messages, "test-model")

        # summary + ack + cleaned recent (user + assistant, no tool/tool_calls)
        assert len(result) == 4
        for msg in result[2:]:
            assert msg.get("role") != "tool"
            assert "tool_calls" not in msg

    def test_find_turn_starts(self):
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        assert find_turn_starts(messages) == [0, 2]

    def test_strip_tool_messages(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "out"},
            {"role": "assistant", "content": "done"},
        ]
        cleaned = strip_tool_messages(messages)
        assert len(cleaned) == 2
        assert cleaned[0]["role"] == "user"
        assert cleaned[1]["content"] == "done"

    def test_format_messages_for_summary(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "tool_call_id": "1", "content": "output"},
        ]
        text = format_messages_for_summary(messages)
        assert "[User] hello" in text
        assert "[Assistant] hi" in text
        assert "[Tool] output" in text

    def test_format_messages_with_multimodal(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ]},
        ]
        text = format_messages_for_summary(messages)
        assert "look at this" in text
        assert "[image attached]" in text


# ---- CompactTool ----


class TestCompactTool:
    def test_no_params_schema(self):
        mgr = CompactManager(MockProvider())
        tool = CompactTool(lambda: [], mgr)
        schema = tool.to_schema()
        assert schema["function"]["parameters"]["properties"] == {}
        assert schema["function"]["parameters"]["required"] == []

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        mgr = CompactManager(MockProvider())
        tool = CompactTool(lambda: [], mgr)
        result = await tool.run_async({})
        assert result == "No messages to compact"

    @pytest.mark.asyncio
    async def test_compacts_and_mutates_in_place(self):
        provider = MockProvider(responses=[Response(content="compressed summary")])
        mgr = CompactManager(provider, keep_recent_turns=0)
        messages = [
            {"role": "user", "content": "fix bug"},
            {"role": "assistant", "content": "fixed"},
            {"role": "user", "content": "add tests"},
            {"role": "assistant", "content": "added"},
        ]
        tool = CompactTool(lambda: messages, mgr)
        result = await tool.run_async({})

        assert "compacted" in result.lower()
        # Messages list should be mutated in place: [summary_msg, ack_msg]
        assert len(messages) == 2
        assert "[Context Summary]" in messages[0]["content"]
