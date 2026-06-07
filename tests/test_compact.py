"""Tests for compact — compact_messages, CompactHook."""

import pytest

from mocode.core import AgentHookContext, Response, Usage
from mocode.core.compact import (
    compact_messages,
    format_messages_for_summary,
    _format_content_parts,
    _format_tool_calls,
)
from mocode.hooks.compact import CompactHook


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


class MockAgent:
    """Minimal agent-like object with a .provider attribute."""

    def __init__(self, provider):
        self.provider = provider


# ---- _format_content_parts ----


class TestFormatContentParts:
    def test_text_only(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        assert _format_content_parts(content) == "hello world"

    def test_with_image(self):
        content = [
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
        ]
        assert _format_content_parts(content) == "look at this [image attached]"

    def test_with_attachment(self):
        content = [
            {"type": "text", "text": "file:"},
            {"type": "file", "file": {"name": "doc.pdf"}},
        ]
        assert _format_content_parts(content) == "file: [attachment]"

    def test_mixed_types(self):
        content = [
            {"type": "text", "text": "a"},
            {"type": "image_url", "image_url": {"url": "..."}},
            {"type": "unknown", "data": "..."},
            "raw string",
        ]
        assert _format_content_parts(content) == "a [image attached] [attachment] raw string"

    def test_non_dict_items(self):
        content = ["plain text", 42, None]
        result = _format_content_parts(content)
        assert "plain text" in result
        assert "42" in result
        assert "None" in result

    def test_empty_list(self):
        assert _format_content_parts([]) == ""


# ---- _format_tool_calls ----


class TestFormatToolCalls:
    def test_basic(self):
        tool_calls = [
            {"function": {"name": "read", "arguments": '{"path": "f.py"}'}},
            {"function": {"name": "write", "arguments": '{"path": "f.py", "content": "x"}'}},
        ]
        result = _format_tool_calls(tool_calls)
        assert "[Tool Call: read(" in result
        assert "[Tool Call: write(" in result
        assert result.count("[Tool Call:") == 2

    def test_skips_non_dict(self):
        tool_calls = [
            {"function": {"name": "read", "arguments": "{}"}},
            "not a dict",
            42,
        ]
        result = _format_tool_calls(tool_calls)
        assert result.count("[Tool Call:") == 1

    def test_missing_function_fields(self):
        tool_calls = [{"function": {}}]
        result = _format_tool_calls(tool_calls)
        assert "[Tool Call: unknown()]" in result

    def test_empty_list(self):
        assert _format_tool_calls([]) == ""


# ---- format_messages_for_summary (integration) ----


class TestFormatMessagesForSummary:
    def test_user_list_content(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "see image"},
                {"type": "image_url", "image_url": {"url": "..."}},
            ]},
        ]
        result = format_messages_for_summary(messages)
        assert "[User] see image [image attached]" in result

    def test_assistant_tool_calls(self):
        messages = [
            {"role": "assistant", "content": "let me check", "tool_calls": [
                {"function": {"name": "read", "arguments": '{"path": "x"}'}},
            ]},
        ]
        result = format_messages_for_summary(messages)
        assert "[Assistant] let me check" in result
        assert "[Tool Call: read(" in result

    def test_tool_role(self):
        messages = [{"role": "tool", "content": "file contents here"}]
        result = format_messages_for_summary(messages)
        assert "[Tool] file contents here" in result

    def test_multiple_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "content": "result"},
        ]
        result = format_messages_for_summary(messages)
        assert result.count("\n\n") == 2  # 3 parts joined by double newline


# ---- compact_messages (pure function) ----


class TestCompactMessages:
    @pytest.mark.asyncio
    async def test_generates_summary(self):
        provider = MockProvider(responses=[Response(content="Summary of conversation")])

        messages = [
            {"role": "user", "content": "Fix the auth bug"},
            {"role": "assistant", "content": "I fixed it in auth.py"},
            {"role": "user", "content": "Now add tests"},
            {"role": "assistant", "content": "Added test_auth.py"},
        ]
        result = await compact_messages(provider, messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert "Summary of conversation" in result[0]["content"]


# ---- CompactHook ----


class TestCompactHook:
    @pytest.mark.asyncio
    async def test_before_iteration_compacts_when_over_threshold(self):
        provider = MockProvider(responses=[Response(content="summary")])
        hook = CompactHook(MockAgent(provider), threshold=0.80, context_window=128_000)
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
        hook = CompactHook(
            MockAgent(MockProvider()), threshold=0.80, context_window=128_000
        )
        hook._last_prompt_tokens = 10_000

        messages = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "resp"},
        ]
        ctx = AgentHookContext(messages=messages)
        await hook.before_iteration(ctx)
        assert ctx.messages is messages

