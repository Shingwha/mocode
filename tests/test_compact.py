"""Tests for compact — compact_messages, CompactHook, CompactTool."""

import pytest

from mocode.core import AgentHookContext, Response, Usage
from mocode.tools.compact import (
    compact_messages, CompactHook, CompactTool,
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


# ---- compact_messages (pure function) ----


class TestCompactMessages:
    @pytest.mark.asyncio
    async def test_returns_original_when_few_turns(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = await compact_messages(MockProvider(), messages, keep_recent_turns=2)
        assert result is messages

    @pytest.mark.asyncio
    async def test_generates_summary(self):
        provider = MockProvider(responses=[Response(content="Summary of conversation")])

        messages = [
            {"role": "user", "content": "Fix the auth bug"},
            {"role": "assistant", "content": "I fixed it in auth.py"},
            {"role": "user", "content": "Now add tests"},
            {"role": "assistant", "content": "Added test_auth.py"},
        ]
        result = await compact_messages(provider, messages, keep_recent_turns=0)

        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert "Summary of conversation" in result[0]["content"]
        assert result[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_fallback_summary(self):
        provider = MockProvider(responses=[Response(content="")])

        messages = [
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = await compact_messages(provider, messages, keep_recent_turns=0)

        assert len(result) == 2
        assert "Conversation summary" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_strips_tool_messages_from_recent(self):
        provider = MockProvider(responses=[Response(content="summary")])

        messages = [
            {"role": "user", "content": "old message"},
            {"role": "assistant", "content": "old response"},
            {"role": "user", "content": "run the tool"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "output"},
            {"role": "assistant", "content": "done"},
        ]
        result = await compact_messages(provider, messages, keep_recent_turns=1)

        assert len(result) == 4
        for msg in result[2:]:
            assert msg.get("role") != "tool"
            assert "tool_calls" not in msg


# ---- CompactHook ----


class TestCompactHook:
    def test_default_context_window(self):
        hook = CompactHook(MockProvider())
        assert hook._context_window == 128_000

    def test_custom_context_window(self):
        hook = CompactHook(MockProvider(), context_window=200_000)
        assert hook._context_window == 200_000

    @pytest.mark.asyncio
    async def test_before_iteration_reads_usage(self):
        hook = CompactHook(MockProvider(), threshold=0.80, context_window=128_000)
        assert hook._last_prompt_tokens == 0

        ctx = AgentHookContext(usage=Usage(prompt_tokens=50000, completion_tokens=100))
        await hook.before_iteration(ctx)
        assert hook._last_prompt_tokens == 50000

    @pytest.mark.asyncio
    async def test_before_iteration_compacts_when_over_threshold(self):
        provider = MockProvider(responses=[Response(content="summary")])
        hook = CompactHook(provider, threshold=0.80, context_window=128_000)
        hook._last_prompt_tokens = 110_000

        messages = [
            {"role": "user", "content": "old 1"},
            {"role": "assistant", "content": "resp 1"},
            {"role": "user", "content": "old 2"},
            {"role": "assistant", "content": "resp 2"},
            {"role": "user", "content": "old 3"},
            {"role": "assistant", "content": "resp 3"},
        ]
        original_len = len(messages)
        ctx = AgentHookContext(messages=messages)
        await hook.before_iteration(ctx)
        assert len(ctx.messages) < original_len

    @pytest.mark.asyncio
    async def test_before_iteration_skips_when_under_threshold(self):
        hook = CompactHook(MockProvider(), threshold=0.80, context_window=128_000)
        hook._last_prompt_tokens = 10_000

        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "resp"},
        ]
        ctx = AgentHookContext(messages=messages)
        await hook.before_iteration(ctx)
        assert ctx.messages is messages

    @pytest.mark.asyncio
    async def test_on_compact_called(self):
        provider = MockProvider(responses=[Response(content="summary")])
        compact_events = []

        class TrackingHook(CompactHook):
            async def on_compact(self, ctx):
                compact_events.append((ctx.compact_old, ctx.compact_new))

        hook = TrackingHook(provider, threshold=0.80, context_window=128_000)
        hook._last_prompt_tokens = 110_000

        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        ctx = AgentHookContext(messages=messages)
        await hook.before_iteration(ctx)
        assert len(compact_events) == 1
        assert compact_events[0][0] == 4
        assert compact_events[0][1] == 2


# ---- CompactTool ----


class TestCompactTool:
    def test_no_params_schema(self):
        tool = CompactTool(MockProvider(), lambda: [])
        schema = tool.to_schema()
        assert schema["function"]["parameters"]["properties"] == {}
        assert schema["function"]["parameters"]["required"] == []

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        tool = CompactTool(MockProvider(), lambda: [])
        result = await tool.run_async({})
        assert result == "No messages to compact"

    @pytest.mark.asyncio
    async def test_compacts_and_mutates_in_place(self):
        provider = MockProvider(responses=[Response(content="compressed summary")])
        messages = [
            {"role": "user", "content": "fix bug"},
            {"role": "assistant", "content": "fixed"},
            {"role": "user", "content": "add tests"},
            {"role": "assistant", "content": "added"},
        ]
        tool = CompactTool(provider, lambda: messages, keep_recent_turns=0)
        result = await tool.run_async({})

        assert "compacted" in result.lower()
        assert len(messages) == 2
        assert "[Context Summary]" in messages[0]["content"]


# ---- Pure helpers (unchanged) ----


class TestPureHelpers:
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
