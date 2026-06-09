"""Tests for compact — preprocessing, fallback, compact_messages, CompactHook."""

import pytest

from mocode.core import IterationContext, CompactContext, Response, Usage
from mocode.core.compact import (
    TOOL_RESULT_MAX_LEN,
    TOOL_ARGS_MAX_LEN,
    _format_content_parts,
    _format_tool_calls,
    _truncate_tool_calls,
    _truncate_tool_result,
    _preprocess_messages,
    format_messages_for_summary,
    build_fallback_summary,
    compact_messages,
)
from mocode.hooks.compact import CompactHook
from mocode.prompts.compact import summary_system_prompt, COMPACT_USER_TEMPLATE


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


# ---- _truncate_tool_result ----


class TestTruncateToolResult:
    def test_short_unchanged(self):
        content = "short content"
        assert _truncate_tool_result(content) == content

    def test_exact_limit_unchanged(self):
        content = "x" * TOOL_RESULT_MAX_LEN
        assert _truncate_tool_result(content) == content

    def test_long_truncated(self):
        content = "a" * 2000
        result = _truncate_tool_result(content)
        assert len(result) < len(content)
        assert result.startswith("a" * (TOOL_RESULT_MAX_LEN // 2))
        assert result.endswith("a" * (TOOL_RESULT_MAX_LEN // 2))
        assert "[truncated to" in result

    def test_preserves_head_and_tail(self):
        head = "START_MARKER_" + "x" * 300
        tail = "y" * 300 + "_END_MARKER"
        content = head + "z" * 2000 + tail
        result = _truncate_tool_result(content)
        assert "START_MARKER_" in result
        assert "_END_MARKER" in result


# ---- _truncate_tool_calls ----


class TestTruncateToolCalls:
    def test_short_args_unchanged(self):
        tc = [{"function": {"name": "read", "arguments": '{"path": "f.py"}'}}]
        result = _truncate_tool_calls(tc)
        assert result[0]["function"]["arguments"] == '{"path": "f.py"}'

    def test_long_args_truncated(self):
        long_args = '{"content": "' + "x" * 500 + '"}'
        tc = [{"function": {"name": "write", "arguments": long_args}}]
        result = _truncate_tool_calls(tc)
        args = result[0]["function"]["arguments"]
        assert len(args) <= TOOL_ARGS_MAX_LEN + len("... [truncated]")
        assert args.endswith("... [truncated]")

    def test_preserves_name(self):
        long_args = "x" * 500
        tc = [{"function": {"name": "edit", "arguments": long_args}}]
        result = _truncate_tool_calls(tc)
        assert result[0]["function"]["name"] == "edit"

    def test_non_dict_passthrough(self):
        tc = ["not a dict", 42]
        result = _truncate_tool_calls(tc)
        assert result == ["not a dict", 42]

    def test_does_not_mutate_input(self):
        long_args = "x" * 500
        tc = [{"function": {"name": "write", "arguments": long_args}}]
        original_args = tc[0]["function"]["arguments"]
        _truncate_tool_calls(tc)
        assert tc[0]["function"]["arguments"] == original_args


# ---- _preprocess_messages ----


class TestPreprocessMessages:
    def test_truncates_long_tool_result(self):
        long_content = "x" * 2000
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read", "arguments": '{"path": "f"}'}}
            ]},
            {"role": "tool", "content": long_content},
        ]
        result = _preprocess_messages(messages)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert len(tool_msgs[0]["content"]) < len(long_content)
        assert "[truncated to" in tool_msgs[0]["content"]

    def test_short_tool_result_unchanged(self):
        messages = [
            {"role": "tool", "content": "short"},
        ]
        result = _preprocess_messages(messages)
        assert result[0]["content"] == "short"

    def test_truncates_long_tool_args(self):
        long_args = '{"content": "' + "y" * 500 + '"}'
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "write", "arguments": long_args}}
            ]},
        ]
        result = _preprocess_messages(messages)
        args = result[0]["tool_calls"][0]["function"]["arguments"]
        assert len(args) < len(long_args)
        assert args.endswith("... [truncated]")

    def test_user_messages_unchanged(self):
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "response"},
        ]
        result = _preprocess_messages(messages)
        assert result == messages

    def test_empty_messages(self):
        assert _preprocess_messages([]) == []

    def test_preserves_all_messages(self):
        """Messages are truncated but count stays the same."""
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read", "arguments": '{"path": "a"}'}}
            ]},
            {"role": "tool", "content": "content A"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read", "arguments": '{"path": "b"}'}}
            ]},
            {"role": "tool", "content": "content B"},
        ]
        result = _preprocess_messages(messages)
        assert len(result) == 4


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


# ---- build_fallback_summary ----


class TestBuildFallbackSummary:
    def test_basic_structure(self):
        messages = [
            {"role": "user", "content": "Fix the auth bug in login.py"},
            {"role": "assistant", "content": "Found the issue in authenticate()"},
            {"role": "assistant", "content": "Fixed by adding null check", "tool_calls": [
                {"function": {"name": "edit", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "Done, the bug is fixed."},
        ]
        result = build_fallback_summary(messages)
        assert "[Conversation summary" in result
        assert "[Intent]" in result
        assert "Fix the auth bug" in result
        assert "[Last State]" in result
        assert "Done, the bug is fixed." in result
        assert "[Tool Usage]" in result
        assert "edit: 1" in result

    def test_user_list_content(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image_url", "image_url": {"url": "..."}},
            ]},
        ]
        result = build_fallback_summary(messages)
        assert "look at this" in result
        assert "[image attached]" in result

    def test_multiple_user_messages(self):
        messages = [
            {"role": "user", "content": "First request"},
            {"role": "assistant", "content": "did first"},
            {"role": "user", "content": "Second request"},
            {"role": "assistant", "content": "did second"},
        ]
        result = build_fallback_summary(messages)
        assert "[User Messages]" in result
        assert "First request" in result
        assert "Second request" in result

    def test_empty_messages(self):
        result = build_fallback_summary([])
        assert "[Conversation summary (0 messages compressed)]" in result

    def test_tool_call_counting(self):
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read", "arguments": "{}"}},
                {"function": {"name": "read", "arguments": "{}"}},
                {"function": {"name": "edit", "arguments": "{}"}},
            ]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "bash", "arguments": "{}"}},
            ]},
            {"role": "tool", "content": "ok"},
        ]
        result = build_fallback_summary(messages)
        assert "read: 2" in result
        assert "edit: 1" in result
        assert "bash: 1" in result


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
        result = await compact_messages(
            provider, messages,
            summary_system_prompt.build(fmt="xml"),
            COMPACT_USER_TEMPLATE,
        )

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "[Context Summary]" in result[0]["content"]
        assert "Summary of conversation" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_short_messages_returned_unchanged(self):
        provider = MockProvider()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = await compact_messages(provider, messages, "", "")
        assert result == messages

    @pytest.mark.asyncio
    async def test_fallback_used_on_provider_error(self):
        class FailProvider:
            async def call(self, **kwargs):
                raise RuntimeError("provider down")

        messages = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "working on it"},
            {"role": "user", "content": "Any progress?"},
            {"role": "assistant", "content": "still debugging"},
        ]
        result = await compact_messages(FailProvider(), messages, "", "")
        assert len(result) == 1
        assert "[Context Summary]" in result[0]["content"]
        # Fallback should include user intent
        assert "Fix the bug" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_preprocessing_applied_before_summary(self):
        """Verify that long tool results are truncated before being sent to LLM."""
        captured_text = []

        class CaptureProvider:
            async def call(self, messages, system, tools, max_tokens):
                captured_text.append(messages[0]["content"])
                return Response(content="captured")

        provider = CaptureProvider()
        long_content = "x" * 5000
        messages = [
            {"role": "user", "content": "read a big file"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read", "arguments": '{"path": "big.txt"}'}}
            ]},
            {"role": "tool", "content": long_content},
            {"role": "user", "content": "now summarize it"},
            {"role": "assistant", "content": "summary done"},
        ]
        await compact_messages(provider, messages, "", "")

        assert len(captured_text) == 1
        # The long content should have been truncated
        assert len(captured_text[0]) < len(long_content) + 1000  # some overhead for formatting


# ---- CompactHook ----


class TestCompactHook:
    @pytest.mark.asyncio
    async def test_before_iteration_sets_flag_when_over_threshold(self):
        provider = MockProvider(responses=[Response(content="summary")])
        hook = CompactHook(MockAgent(provider), threshold=0.80, context_window=128_000)
        hook._last_prompt_tokens = 110_000

        messages = [
            {"role": "user", "content": "old 1"},
            {"role": "assistant", "content": "resp 1"},
        ]
        ctx = IterationContext(messages=messages)
        await hook.before_iteration(ctx)
        assert ctx._needs_compact is True
        # Messages unchanged — compaction happens in on_compact
        assert len(ctx.messages) == 2

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
        ctx = IterationContext(messages=messages)
        await hook.before_iteration(ctx)
        assert ctx._needs_compact is False
        assert ctx.messages is messages

    @pytest.mark.asyncio
    async def test_on_compact_replaces_messages(self):
        provider = MockProvider(responses=[Response(content="summary")])
        hook = CompactHook(MockAgent(provider), threshold=0.80, context_window=128_000)

        messages = [
            {"role": "user", "content": "old 1"},
            {"role": "assistant", "content": "resp 1"},
            {"role": "user", "content": "old 2"},
            {"role": "assistant", "content": "resp 2"},
        ]
        original_len = len(messages)
        ctx = CompactContext(messages=messages, old_count=original_len)
        await hook.on_compact(ctx)
        assert len(messages) < original_len
        # new_count is set by the caller (agent loop), not the hook
        assert hook._last_prompt_tokens == 0
